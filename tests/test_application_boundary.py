"""Phase 4 production architecture boundaries (static AST checks).

Scans production code only: every module under baec_app/ and scripts/.
tests/ is exempt. Each rule is also run against synthetic violating source
to prove the scanner reports it. Rules are added increment by increment;
4A covers layering (R6), data-free foundation modules (R7), canonical
serialization purity, and the two Phase 4 proposal origins; 4B adds R4
(the gate sentinel and HumanApproval construction stay inside approval.py);
4C adds R1, R2, and R5 (HumanAuthorization construction and authority
imports) and the (self, approval) command signatures; 4D admits
account_state.py to R5 and limits application use of TransitionRejectionKind
to AUTHORIZATION_MISSING; 4E adds R3 (future interfaces never import the data
layer), no model/MCP imports, no parse_proposal, no authority-style
parameters, and the facade command signatures.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from baec_app.application.account_state import AccountStatePreviewService, AccountStateService
from baec_app.application.classification import ClassificationService
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.application.facades import HumanCommandFacade, ProposalFacade, ReadService
from baec_app.application.proposals import ProposalOrigin
from baec_app.domain.enums import TransitionRejectionKind

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (REPO_ROOT / "baec_app", REPO_ROOT / "scripts")

# --- scanner --------------------------------------------------------------------


@dataclass
class Facts:
    module: str
    imports: list[tuple[str, int]] = field(default_factory=list)  # fully qualified targets
    runtime_imports: list[tuple[str, int]] = field(default_factory=list)  # imports outside `if TYPE_CHECKING:`
    calls: list[tuple[str, int]] = field(default_factory=list)  # resolved dotted callee names
    call_first_args: list[tuple[str, str, int]] = field(default_factory=list)  # (callee, resolved first argument)
    attributes: list[tuple[str, int]] = field(default_factory=list)  # attribute names accessed
    names: list[tuple[str, int]] = field(default_factory=list)  # bare names referenced
    strings: list[tuple[str, int]] = field(default_factory=list)  # string constants, for exact-identifier rules only
    references: list[tuple[str, str | None, int]] = field(default_factory=list)  # (resolved name, member read from it)
    getattr_strings: list[tuple[str, str, int]] = field(default_factory=list)  # getattr(object, "literal")
    value_comparisons: list[tuple[str, int]] = field(default_factory=list)  # literals compared with some `x.value`
    definitions: list[tuple[str, int]] = field(default_factory=list)  # function and class names defined
    parameters: list[tuple[str, str, int]] = field(default_factory=list)  # (public function, parameter name)
    dynamic_imports: list[tuple[str, int]] = field(default_factory=list)  # string arguments to import calls


DYNAMIC_IMPORT_CALLS = ("importlib.import_module", "importlib.__import__", "__import__")


def _string_literals(node: ast.AST) -> list[str]:
    """A string constant, or the string constants of a literal set, tuple, or list."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _is_type_checking(test: ast.AST) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str:
    parts = module.split(".")
    package = parts if is_package else parts[:-1]
    base = package[: len(package) - (level - 1)] if level > 1 else package
    return ".".join(base + ([target] if target else []))


def scan(source: str, module: str, is_package: bool = False) -> Facts:
    """Collect imports, resolved calls, attribute names, and dynamic-import targets."""
    tree = ast.parse(source)
    facts = Facts(module)
    aliases: dict[str, str] = {}
    type_checking_only = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and _is_type_checking(node.test)
        for statement in node.body
        for inner in ast.walk(statement)
    }

    def add_import(target: str, node: ast.AST) -> None:
        facts.imports.append((target, node.lineno))
        if id(node) not in type_checking_only:
            facts.runtime_imports.append((target, node.lineno))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                add_import(name.name, node)
                aliases[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = _resolve_relative(module, is_package, node.level, node.module)
            add_import(base, node)
            for name in node.names:
                full = f"{base}.{name.name}" if base else name.name
                add_import(full, node)
                aliases[name.asname or name.name] = full
        elif isinstance(node, ast.Attribute):
            facts.attributes.append((node.attr, node.lineno))
        elif isinstance(node, ast.Name):
            facts.names.append((node.id, node.lineno))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            facts.strings.append((node.value, node.lineno))

    def dotted(expr: ast.AST) -> str | None:
        if isinstance(expr, ast.Name):
            return aliases.get(expr.id, expr.id)
        if isinstance(expr, ast.Attribute):
            base = dotted(expr.value)
            return f"{base}.{expr.attr}" if base else None
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            facts.definitions.append((node.name, node.lineno))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            arguments = node.args
            for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs, arguments.vararg, arguments.kwarg):
                if argument is not None:
                    facts.parameters.append((node.name, argument.arg, node.lineno))

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(isinstance(o, ast.Attribute) and o.attr == "value" for o in operands):
                for operand in operands:
                    for literal in _string_literals(operand):
                        facts.value_comparisons.append((literal, node.lineno))

    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)):
            resolved = dotted(node)
            if resolved:
                parent = parents.get(node)
                member = parent.attr if isinstance(parent, ast.Attribute) and parent.value is node else None
                facts.references.append((resolved, member, node.lineno))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name:
                facts.calls.append((name, node.lineno))
                if node.args:
                    first = dotted(node.args[0])
                    if first:
                        facts.call_first_args.append((name, first, node.lineno))
            # Only a string passed to a recognized import call is a dynamic import;
            # docstrings and other strings that merely mention a package are not.
            if name == "getattr" and len(node.args) >= 2:
                literal = node.args[1]
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                    facts.getattr_strings.append((dotted(node.args[0]) or "", literal.value, node.lineno))
            if name in DYNAMIC_IMPORT_CALLS and node.args:
                target = node.args[0]
                if isinstance(target, ast.Constant) and isinstance(target.value, str):
                    facts.dynamic_imports.append((target.value, node.lineno))
    return facts


def _in_package(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def imports_package(facts: Facts, package: str) -> list[str]:
    """Imports of a package, static or through a recognized dynamic-import call."""
    found = [f"{facts.module}:{line} imports {name}" for name, line in facts.imports if _in_package(name, package)]
    found += [
        f"{facts.module}:{line} dynamically imports {name}"
        for name, line in facts.dynamic_imports
        if _in_package(name, package)
    ]
    return found


def production_modules() -> list[tuple[str, Facts]]:
    modules = []
    for root in PRODUCTION_ROOTS:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(REPO_ROOT).with_suffix("")
            parts = list(relative.parts)
            is_package = parts[-1] == "__init__"
            if is_package:
                parts = parts[:-1]
            module = ".".join(parts)
            modules.append((module, scan(path.read_text(encoding="utf-8"), module, is_package)))
    return modules


# --- rules ----------------------------------------------------------------------

DATA_FREE_MODULES = (
    "baec_app.application.proposals",
    "baec_app.application.requests",
    "baec_app.application.canonical",
    "baec_app.application.context",
    "baec_app.application.errors",
)

FORBIDDEN_IN_CANONICAL_CALLS = (
    "dataclasses.asdict",
    "dataclasses.astuple",
    "dataclasses.fields",
    "repr",
    "vars",
)
FORBIDDEN_IN_CANONICAL_IMPORTS = ("pickle", "copyreg", "marshal", "shelve", "dataclasses")


def rule_r6_layering(facts: Facts) -> list[str]:
    """domain imports neither data nor application; data does not import application."""
    if _in_package(facts.module, "baec_app.domain"):
        return imports_package(facts, "baec_app.data") + imports_package(facts, "baec_app.application")
    if _in_package(facts.module, "baec_app.data"):
        return imports_package(facts, "baec_app.application")
    return []


def rule_r7_data_free(facts: Facts) -> list[str]:
    """Foundation application modules import nothing from the data layer."""
    if facts.module in DATA_FREE_MODULES:
        return imports_package(facts, "baec_app.data")
    return []


def rule_canonical_purity(facts: Facts) -> list[str]:
    """Canonical serialization uses explicit field tables only: no generic introspection or pickling."""
    if facts.module != "baec_app.application.canonical":
        return []
    found = [f"{facts.module}:{line} calls {name}" for name, line in facts.calls if name in FORBIDDEN_IN_CANONICAL_CALLS]
    for package in FORBIDDEN_IN_CANONICAL_IMPORTS:
        found += imports_package(facts, package)
    found += [f"{facts.module}:{line} accesses {attr}" for attr, line in facts.attributes if attr in ("__dict__", "__dataclass_fields__")]
    return found


GATE_MODULE = "baec_app.application.approval"
GATE_KEY = "_GATE_KEY"


def _is_symbol(dotted_name: str, symbol: str) -> bool:
    return dotted_name == symbol or dotted_name.endswith("." + symbol)


def rule_r4_gate_key(facts: Facts) -> list[str]:
    """_GATE_KEY is private to approval.py: no import, name, attribute, or string reference elsewhere."""
    if facts.module == GATE_MODULE:
        return []
    found = [f"{facts.module}:{line} imports {name}" for name, line in facts.imports if _is_symbol(name, GATE_KEY)]
    found += [f"{facts.module}:{line} names {GATE_KEY}" for name, line in facts.names if name == GATE_KEY]
    found += [f"{facts.module}:{line} accesses .{GATE_KEY}" for attr, line in facts.attributes if attr == GATE_KEY]
    found += [f"{facts.module}:{line} spells {GATE_KEY!r}" for text, line in facts.strings if text == GATE_KEY]
    return found


def rule_r4_human_approval_construction(facts: Facts) -> list[str]:
    """Only approval.py constructs HumanApproval, directly or through __new__."""
    if facts.module == GATE_MODULE:
        return []
    found = [
        f"{facts.module}:{line} calls {name}"
        for name, line in facts.calls
        if _is_symbol(name, "HumanApproval") or _is_symbol(name, "HumanApproval.__new__")
    ]
    found += [
        f"{facts.module}:{line} calls {name}({first})"
        for name, first, line in facts.call_first_args
        if _is_symbol(name, "__new__") and _is_symbol(first, "HumanApproval")
    ]
    return found


AUTHORIZATION_CONSTRUCTORS = (
    "baec_app.data.repository",  # rehydration of stored authorizations (Phase 3)
    "baec_app.data.seed",  # synthetic seed fixtures (Phase 3)
    "baec_app.application.authority",  # the only application construction site
)
AUTHORITY_MODULE = "baec_app.application.authority"
AUTHORITY_IMPORTERS = (
    "baec_app.application.classification",
    "baec_app.application.dormancy",
    "baec_app.application.account_state",
    "baec_app.application.proposal_authorization",  # Phase 7F-B: authorize_grant, for persisted grants only
)


def rule_r1_authorization_construction(facts: Facts) -> list[str]:
    """HumanAuthorization is constructed only in the approved modules (directly, by alias, or via __new__)."""
    if facts.module in AUTHORIZATION_CONSTRUCTORS:
        return []
    found = [
        f"{facts.module}:{line} calls {name}"
        for name, line in facts.calls
        if _is_symbol(name, "HumanAuthorization") or _is_symbol(name, "HumanAuthorization.__new__")
    ]
    found += [
        f"{facts.module}:{line} calls {name}({first})"
        for name, first, line in facts.call_first_args
        if _is_symbol(name, "__new__") and _is_symbol(first, "HumanAuthorization")
    ]
    return found


def rule_r2_runtime_authorization_import(facts: Facts) -> list[str]:
    """Inside the application layer, only authority.py imports HumanAuthorization at runtime."""
    if not _in_package(facts.module, "baec_app.application") or facts.module == AUTHORITY_MODULE:
        return []
    return [
        f"{facts.module}:{line} imports {name} at runtime"
        for name, line in facts.runtime_imports
        if _is_symbol(name, "HumanAuthorization")
    ]


def rule_r5_authority_importers(facts: Facts) -> list[str]:
    """application.authority is imported only by the command services."""
    if facts.module in AUTHORITY_IMPORTERS or facts.module == AUTHORITY_MODULE:
        return []
    return imports_package(facts, AUTHORITY_MODULE)


ALLOWED_REJECTION_MEMBER = "AUTHORIZATION_MISSING"
REJECTION_ENUM = "TransitionRejectionKind"
FORBIDDEN_REJECTION_VALUES = {k.value for k in TransitionRejectionKind if k.name != ALLOWED_REJECTION_MEMBER}


def rule_rejection_kind_usage(facts: Facts) -> list[str]:
    """Application code reads TransitionRejectionKind only as .AUTHORIZATION_MISSING.

    Reported: any other member (direct, aliased, module-qualified); any use of
    the enum that is not exactly .AUTHORIZATION_MISSING, which covers
    iteration, indexing such as K["SAME_STATE"], and getattr(K, ...); and
    obtaining the enum itself reflectively, as in getattr(enums,
    "TransitionRejectionKind"); and comparing or testing membership of any
    `x.value` against another rejection's value string, as in
    r.value == "SAME_STATE" or r.value in {"ADDRESSABILITY_NO"}. Ordinary
    strings and docstrings that merely mention a rejection name are not reported.
    """
    if not _in_package(facts.module, "baec_app.application"):
        return []
    found = [
        f"{facts.module}:{line} uses {name}" + (f".{member}" if member else " other than as .AUTHORIZATION_MISSING")
        for name, member, line in facts.references
        if _is_symbol(name, REJECTION_ENUM) and member != ALLOWED_REJECTION_MEMBER
    ]
    found += [
        f"{facts.module}:{line} reflectively reads {obj}.{text}"
        for obj, text, line in facts.getattr_strings
        if text == REJECTION_ENUM
    ]
    found += [
        f"{facts.module}:{line} compares a .value with rejection value {text!r}"
        for text, line in facts.value_comparisons
        if text in FORBIDDEN_REJECTION_VALUES
    ]
    return found


def rule_r3_interfaces_do_not_import_data(facts: Facts) -> list[str]:
    """Future interface code (baec_app/interfaces/**) imports application and domain, never the data layer."""
    if not _in_package(facts.module, "baec_app.interfaces"):
        return []
    return imports_package(facts, "baec_app.data")


MODEL_PACKAGES = ("anthropic", "claude_agent_sdk", "claude_code_sdk", "openai", "litellm", "langchain")
MCP_PACKAGES = ("mcp", "fastmcp")
MCP_INTERFACE_PACKAGE = "baec_app.mcp"  # the approved Phase 5 MCP Core package (docs/PHASE5_MCP_CORE_DESIGN.md)
# The approved Phase 7G one-tool write package (docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §16.3).
MCP_WRITE_PACKAGE = "baec_app.mcp_write"
# The one approved Phase 6 provider adapter (docs/PHASE6C_ANTHROPIC_SDK_CHARACTERIZATION.md §11). Exact module only.
ANTHROPIC_PROVIDER_MODULE = "baec_app.ai.anthropic_provider"


def rule_no_model_or_mcp_integration(facts: Facts) -> list[str]:
    """No production code imports a model SDK, statically or dynamically.

    The one exception: the anthropic SDK may be imported by exactly
    baec_app.ai.anthropic_provider (Phase 6). The MCP SDK may be imported only by
    the approved Phase 5 MCP interface package (baec_app.mcp) and, since Phase 7G,
    the one-tool write package (baec_app.mcp_write); the application, domain,
    data, and scripts may not.
    """
    found = []
    for package in MODEL_PACKAGES:
        if package == "anthropic" and facts.module == ANTHROPIC_PROVIDER_MODULE:
            continue
        found += imports_package(facts, package)
    if not (_in_package(facts.module, MCP_INTERFACE_PACKAGE) or _in_package(facts.module, MCP_WRITE_PACKAGE)):
        for package in MCP_PACKAGES:
            found += imports_package(facts, package)
    return found


def rule_no_proposal_parser(facts: Facts) -> list[str]:
    """No mapping-to-proposal parser exists in Phase 4."""
    return [f"{facts.module}:{line} defines {name}" for name, line in facts.definitions if name == "parse_proposal"]


AUTHORITY_PARAMETERS = {"authorization", "authorized_by", "authorized_at", "approved", "human_confirmed", "is_approved"}


def rule_no_authority_parameters(facts: Facts) -> list[str]:
    """No public application or interface function takes an authority-style parameter.

    The legitimate 'approval' argument of authoritative commands and the
    gate's own API are not in this set. Docstrings are never inspected.
    """
    if not (_in_package(facts.module, "baec_app.application") or _in_package(facts.module, "baec_app.interfaces")):
        return []
    return [
        f"{facts.module}:{line} {function}({parameter})"
        for function, parameter, line in facts.parameters
        if parameter in AUTHORITY_PARAMETERS
    ]


RULES = {
    "R6 layering": rule_r6_layering,
    "R7 data-free foundation modules": rule_r7_data_free,
    "canonical purity": rule_canonical_purity,
    "R4 gate key private to approval.py": rule_r4_gate_key,
    "R4 HumanApproval constructed only in approval.py": rule_r4_human_approval_construction,
    "R1 HumanAuthorization constructed only in approved modules": rule_r1_authorization_construction,
    "R2 HumanAuthorization runtime-imported only by authority.py": rule_r2_runtime_authorization_import,
    "R5 authority imported only by the command services": rule_r5_authority_importers,
    "application reads only AUTHORIZATION_MISSING": rule_rejection_kind_usage,
    "R3 interfaces never import the data layer": rule_r3_interfaces_do_not_import_data,
    "no model or MCP integration": rule_no_model_or_mcp_integration,
    "no parse_proposal": rule_no_proposal_parser,
    "no authority-style parameters": rule_no_authority_parameters,
}


# --- production checks ------------------------------------------------------------


def test_scanner_finds_the_production_packages():
    names = {module for module, _ in production_modules()}
    assert "baec_app.domain.models" in names and "baec_app.data.repository" in names
    assert "scripts.seed_demo" in names
    assert not any(_in_package(name, "tests") for name in names)
    assert set(DATA_FREE_MODULES) <= names  # the rule is not vacuous
    assert GATE_MODULE in names


def test_the_approved_authorization_constructors_are_real_and_authority_is_used():
    """Positive controls: R1, R2 and R5 would fire on these modules' own code anywhere else."""
    modules = dict(production_modules())
    for module in AUTHORIZATION_CONSTRUCTORS:
        assert any(_is_symbol(name, "HumanAuthorization") for name, _ in modules[module].calls), module
    authority_source = (REPO_ROOT / "baec_app/application/authority.py").read_text(encoding="utf-8")
    as_facade = scan(authority_source, "baec_app.application.facades")
    assert rule_r1_authorization_construction(as_facade) and rule_r2_runtime_authorization_import(as_facade)
    for module in AUTHORITY_IMPORTERS:
        assert imports_package(modules[module], AUTHORITY_MODULE), module
    classification_source = (REPO_ROOT / "baec_app/application/classification.py").read_text(encoding="utf-8")
    assert rule_r5_authority_importers(scan(classification_source, "baec_app.application.facades"))


COMMANDS = {
    "ClassificationService.confirm": ClassificationService.confirm,
    "DormancyJudgmentService.record": DormancyJudgmentService.record,
    "AccountStateService.move_to_conditionally_dormant": AccountStateService.move_to_conditionally_dormant,
    "AccountStateService.move_to_active_opportunity": AccountStateService.move_to_active_opportunity,
    "AccountStateService.move_to_no_plausible_path": AccountStateService.move_to_no_plausible_path,
    "HumanCommandFacade.confirm_baec": HumanCommandFacade.confirm_baec,
    "HumanCommandFacade.record_dormancy_judgment": HumanCommandFacade.record_dormancy_judgment,
    "HumanCommandFacade.move_to_conditionally_dormant": HumanCommandFacade.move_to_conditionally_dormant,
    "HumanCommandFacade.move_to_active_opportunity": HumanCommandFacade.move_to_active_opportunity,
    "HumanCommandFacade.move_to_no_plausible_path": HumanCommandFacade.move_to_no_plausible_path,
}
AUTHORITY_PARAMETER_NAMES = {"authorization", "approved", "authorized_by", "authorized_at", "actor", "actor_id", "confirmation"}


@pytest.mark.parametrize("command", COMMANDS.values(), ids=COMMANDS.keys())
def test_command_signatures_are_exactly_self_and_approval(command):
    assert list(inspect.signature(command).parameters) == ["self", "approval"]


@pytest.mark.parametrize(
    "service",
    [ClassificationService, DormancyJudgmentService, AccountStateService, HumanCommandFacade, ProposalFacade, ReadService],
    ids=lambda c: c.__name__,
)
def test_no_public_service_method_accepts_authority_by_name_or_annotation(service):
    for name, method in inspect.getmembers(service, inspect.isfunction):
        if name.startswith("_"):
            continue
        for parameter in inspect.signature(method).parameters.values():
            assert parameter.name not in AUTHORITY_PARAMETER_NAMES, (name, parameter.name)
            assert "HumanAuthorization" not in str(parameter.annotation), (name, parameter.name)


def test_every_facade_method_taking_an_approval_takes_only_the_approval():
    for name, method in inspect.getmembers(HumanCommandFacade, inspect.isfunction):
        parameters = list(inspect.signature(method).parameters)
        if "approval" in parameters:
            assert parameters == ["self", "approval"], name
    assert {n for n, m in inspect.getmembers(HumanCommandFacade, inspect.isfunction)
            if "approval" in inspect.signature(m).parameters} == {name.split(".")[1] for name in COMMANDS if name.startswith("HumanCommandFacade")}


def test_the_proposal_facade_has_no_command_or_approval_method():
    for name, method in inspect.getmembers(ProposalFacade, inspect.isfunction):
        assert "approval" not in inspect.signature(method).parameters, name
        assert not name.startswith(("confirm", "record", "move_to", "request", "approve", "redeem")), name


# --- the gate-free preview service (4E refinement) -------------------------------------

PREVIEW_METHODS = {
    "preview_move_to_conditionally_dormant",
    "preview_move_to_active_opportunity",
    "preview_move_to_no_plausible_path",
}
NOT_IN_PREVIEW_SERVICE = {
    "authority", "authorize", "HumanConfirmationGate", "HumanApproval", "_gate", "gate", "redeem", "register",
    "approve", "build_request", "_clock", "_ids", "clock", "ids", "persist_transition_to_conditionally_dormant",
    "persist_transition_to_active_opportunity", "persist_transition_to_no_plausible_path", "save_confirmed_baec",
    "save_classification_record", "record_dormancy_judgment", "add_account", "add_interaction",
}


def _class_node(module_path: str, class_name: str) -> ast.ClassDef:
    tree = ast.parse((REPO_ROOT / module_path).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == class_name)


def test_the_preview_service_takes_only_a_repository_and_offers_only_previews():
    assert list(inspect.signature(AccountStatePreviewService.__init__).parameters) == ["self", "repository"]
    public = {name for name, _ in inspect.getmembers(AccountStatePreviewService, inspect.isfunction) if not name.startswith("_")}
    assert public == PREVIEW_METHODS


def test_the_preview_service_source_reaches_no_gate_authority_clock_or_write():
    node = _class_node("baec_app/application/account_state.py", "AccountStatePreviewService")
    used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }
    assert used & NOT_IN_PREVIEW_SERVICE == set()


def test_each_transition_preview_has_exactly_one_implementation():
    """The locked transition functions are called only inside AccountStatePreviewService."""
    callers = []
    for path in sorted((REPO_ROOT / "baec_app/application").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)] + [tree]:
            for call in (n for n in ast.walk(cls) if isinstance(n, ast.Call)):
                name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")
                if name.startswith("transition_to_"):
                    callers.append((path.name, getattr(cls, "name", "<module>"), name))
    in_classes = {(f, c, n) for f, c, n in callers if c != "<module>"}
    assert in_classes == {("account_state.py", "AccountStatePreviewService", f"transition_to_{d}")
                          for d in ("conditionally_dormant", "active_opportunity", "no_plausible_path")}
    assert {(f, n) for f, c, n in callers} == {(f, n) for f, c, n in in_classes}  # no module-level callers elsewhere


def test_the_command_service_delegates_previews_instead_of_reimplementing_them():
    node = _class_node("baec_app/application/account_state.py", "AccountStateService")
    defined = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
    assert not defined & {"_require_interaction_of", "_require_evidence_interaction_of"}
    called = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    assert not called & {"get_interaction", "list_dormancy_judgments"}  # its reads happen in the preview service


def test_the_classification_preview_has_one_implementation():
    classification = _class_node("baec_app/application/classification.py", "ClassificationService")
    preview = next(n for n in classification.body if isinstance(n, ast.FunctionDef) and n.name == "preview")
    calls = {n.func.id for n in ast.walk(preview) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert calls == {"preview_classification"}


def test_account_state_reads_authorization_missing_and_nothing_else():
    """Positive control: the only rejection member account_state.py touches is the allowed one."""
    facts = dict(production_modules())["baec_app.application.account_state"]
    used = {member for name, member, _ in facts.references if _is_symbol(name, "TransitionRejectionKind")}
    assert used == {ALLOWED_REJECTION_MEMBER}


def test_the_gate_module_itself_holds_and_uses_the_sentinel():
    """Positive control: the R4 rules would see these references if they were anywhere else."""
    gate = dict(production_modules())[GATE_MODULE]
    assert any(name == GATE_KEY for name, _ in gate.names)
    assert any(_is_symbol(name, "HumanApproval") for name, _ in gate.calls)
    elsewhere = scan((REPO_ROOT / "baec_app/application/approval.py").read_text(encoding="utf-8"), "baec_app.application.dormancy")
    assert rule_r4_gate_key(elsewhere) and rule_r4_human_approval_construction(elsewhere)


@pytest.mark.parametrize("rule", RULES.values(), ids=RULES.keys())
def test_production_code_obeys_the_rule(rule):
    violations = [message for _, facts in production_modules() for message in rule(facts)]
    assert violations == []


def test_proposal_origins_are_exactly_human_draft_and_deterministic():
    """Phase 7C added AI_DRAFT once persistent AI-origin provenance existed (name kept for ID continuity)."""
    assert [o.value for o in ProposalOrigin] == ["HUMAN_DRAFT", "DETERMINISTIC", "AI_DRAFT"], (
        "No other origin may be added; AI_MODEL stays absent (PHASE4 design §18; PHASE7 design §6.4)."
    )


# --- scanner self-tests: each synthetic snippet must be reported --------------------

VIOLATIONS = {
    "domain imports data": ("baec_app.domain.x", "from baec_app.data import repository", rule_r6_layering),
    "domain imports data via alias": ("baec_app.domain.x", "import baec_app.data.repository as r", rule_r6_layering),
    "domain imports data relatively": ("baec_app.domain.x", "from ..data import repository", rule_r6_layering),
    "domain imports application": ("baec_app.domain.x", "from baec_app.application import canonical", rule_r6_layering),
    "domain imports data dynamically": (
        "baec_app.domain.x",
        "import importlib\nimportlib.import_module('baec_app.data.repository')",
        rule_r6_layering,
    ),
    "domain imports data via aliased import_module": (
        "baec_app.domain.x",
        "from importlib import import_module as load\nload('baec_app.data')",
        rule_r6_layering,
    ),
    "domain imports data via aliased importlib": (
        "baec_app.domain.x",
        "import importlib as il\nil.import_module('baec_app.data.database')",
        rule_r6_layering,
    ),
    "domain imports data via __import__": ("baec_app.domain.x", "__import__('baec_app.data.repository')", rule_r6_layering),
    "requests imports data dynamically": (
        "baec_app.application.requests",
        "import importlib\nimportlib.import_module('baec_app.data')",
        rule_r7_data_free,
    ),
    "gate key imported": ("baec_app.application.dormancy", "from baec_app.application.approval import _GATE_KEY", rule_r4_gate_key),
    "gate key imported relatively with alias": (
        "baec_app.application.dormancy",
        "from .approval import _GATE_KEY as key",
        rule_r4_gate_key,
    ),
    "gate key as module attribute": (
        "baec_app.application.facades",
        "import baec_app.application.approval as a\nk = a._GATE_KEY",
        rule_r4_gate_key,
    ),
    "gate key through getattr": (
        "baec_app.interfaces.ui",
        "from baec_app.application import approval\nk = getattr(approval, '_GATE_KEY')",
        rule_r4_gate_key,
    ),
    "gate key in an authority module": ("baec_app.application.authority", "x = _GATE_KEY", rule_r4_gate_key),
    "HumanApproval constructed": (
        "baec_app.application.dormancy",
        "from baec_app.application.approval import HumanApproval\nHumanApproval('a', 'r', 's', 'u', 'd', t, _key=k)",
        rule_r4_human_approval_construction,
    ),
    "HumanApproval constructed via alias": (
        "baec_app.application.account_state",
        "from .approval import HumanApproval as Approval\nApproval(*fields)",
        rule_r4_human_approval_construction,
    ),
    "HumanApproval constructed via module attribute": (
        "baec_app.application.facades",
        "import baec_app.application.approval as gate\ngate.HumanApproval(*fields)",
        rule_r4_human_approval_construction,
    ),
    "HumanApproval.__new__": (
        "baec_app.application.authority",
        "from baec_app.application.approval import HumanApproval\nHumanApproval.__new__(HumanApproval)",
        rule_r4_human_approval_construction,
    ),
    "object.__new__(HumanApproval)": (
        "baec_app.interfaces.ui",
        "from baec_app.application.approval import HumanApproval as H\nobject.__new__(H)",
        rule_r4_human_approval_construction,
    ),
    "HumanAuthorization constructed in a service": (
        "baec_app.application.dormancy",
        "from baec_app.domain.models import HumanAuthorization\nHumanAuthorization('u', t, a, 's')",
        rule_r1_authorization_construction,
    ),
    "HumanAuthorization constructed via alias": (
        "baec_app.application.classification",
        "from baec_app.domain.models import HumanAuthorization as Grant\nGrant('u', t, a, 's')",
        rule_r1_authorization_construction,
    ),
    "HumanAuthorization constructed module-qualified": (
        "baec_app.interfaces.ui",
        "import baec_app.domain.models as m\nm.HumanAuthorization('u', t, a, 's')",
        rule_r1_authorization_construction,
    ),
    "HumanAuthorization constructed relatively in the domain": (
        "baec_app.domain.state_machine",
        "from .models import HumanAuthorization\nHumanAuthorization('u', t, a, 's')",
        rule_r1_authorization_construction,
    ),
    "HumanAuthorization via object.__new__": (
        "baec_app.application.facades",
        "from baec_app.domain import models\nobject.__new__(models.HumanAuthorization)",
        rule_r1_authorization_construction,
    ),
    "HumanAuthorization runtime import in a service": (
        "baec_app.application.dormancy",
        "from baec_app.domain.models import HumanAuthorization",
        rule_r2_runtime_authorization_import,
    ),
    "HumanAuthorization runtime import inside a function": (
        "baec_app.application.classification",
        "def f():\n    from baec_app.domain.models import HumanAuthorization as H\n    return H",
        rule_r2_runtime_authorization_import,
    ),
    "HumanAuthorization imported in the else branch of TYPE_CHECKING": (
        "baec_app.application.requests",
        "if TYPE_CHECKING:\n    pass\nelse:\n    from baec_app.domain.models import HumanAuthorization",
        rule_r2_runtime_authorization_import,
    ),
    "authority imported by a facade": ("baec_app.application.facades", "from baec_app.application import authority", rule_r5_authority_importers),
    "authority function imported relatively": (
        "baec_app.application.composition",
        "from .authority import authorize as grant",
        rule_r5_authority_importers,
    ),
    "authority imported by an interface": (
        "baec_app.interfaces.mcp",
        "import baec_app.application.authority as a",
        rule_r5_authority_importers,
    ),
    "authority imported dynamically": (
        "baec_app.application.facades",
        "import importlib\nimportlib.import_module('baec_app.application.authority')",
        rule_r5_authority_importers,
    ),
    "application reads another rejection member": (
        "baec_app.application.account_state",
        "from baec_app.domain.enums import TransitionRejectionKind\nif TransitionRejectionKind.SAME_STATE in r: pass",
        rule_rejection_kind_usage,
    ),
    "application reads a rejection member through an alias": (
        "baec_app.application.account_state",
        "from baec_app.domain.enums import TransitionRejectionKind as Why\nx = Why.BAEC_NOT_CURRENT",
        rule_rejection_kind_usage,
    ),
    "application reads a rejection member module-qualified": (
        "baec_app.application.account_state",
        "from baec_app.domain import enums\nx = enums.TransitionRejectionKind.PLAUSIBILITY_NOT_YES",
        rule_rejection_kind_usage,
    ),
    "application iterates the rejection kinds": (
        "baec_app.application.account_state",
        "from baec_app.domain.enums import TransitionRejectionKind\nfor k in TransitionRejectionKind: pass",
        rule_rejection_kind_usage,
    ),
    "application indexes the rejection kinds": (
        "baec_app.application.dormancy",
        "from baec_app.domain.enums import TransitionRejectionKind\nk = TransitionRejectionKind['SAME_STATE']",
        rule_rejection_kind_usage,
    ),
    "application getattr on the rejection kinds": (
        "baec_app.application.account_state",
        "from baec_app.domain.enums import TransitionRejectionKind as K\nk = getattr(K, name)",
        rule_rejection_kind_usage,
    ),
    "application getattr of another rejection member by string": (
        "baec_app.application.account_state",
        "from baec_app.domain.enums import TransitionRejectionKind\nk = getattr(TransitionRejectionKind, 'SAME_STATE')",
        rule_rejection_kind_usage,
    ),
    "application getattr by string, module-qualified": (
        "baec_app.application.account_state",
        "from baec_app.domain import enums\nk = getattr(enums.TransitionRejectionKind, 'BAEC_NOT_CURRENT')",
        rule_rejection_kind_usage,
    ),
    "application indexes the rejection kinds through an alias": (
        "baec_app.application.account_state",
        "from baec_app.domain.enums import TransitionRejectionKind as Why\nk = Why['ADDRESSABILITY_NO']",
        rule_rejection_kind_usage,
    ),
    ".value == rejection string": (
        "baec_app.application.account_state",
        "if r.rejections[0].value == 'ADDRESSABILITY_NO': pass",
        rule_rejection_kind_usage,
    ),
    "rejection string == .value": ("baec_app.application.account_state", "ok = 'SAME_STATE' == k.value", rule_rejection_kind_usage),
    ".value != rejection string": ("baec_app.application.dormancy", "ok = k.value != 'BAEC_NOT_CURRENT'", rule_rejection_kind_usage),
    ".value in a set of rejection strings": (
        "baec_app.application.account_state",
        "blocked = any(k.value in {'SAME_STATE', 'ADDRESSABILITY_NO'} for k in r.rejections)",
        rule_rejection_kind_usage,
    ),
    ".value not in a tuple of rejection strings": (
        "baec_app.application.account_state",
        "ok = k.value not in ('REASON_MISSING',)",
        rule_rejection_kind_usage,
    ),
    ".value in a list with an allowed and a forbidden string": (
        "baec_app.application.account_state",
        "ok = k.value in ['AUTHORIZATION_MISSING', 'PLAUSIBILITY_NOT_YES']",
        rule_rejection_kind_usage,
    ),
    "chained comparison with .value": ("baec_app.application.account_state", "ok = a == k.value == 'GROUND_MISSING'", rule_rejection_kind_usage),
    "application obtains the enum reflectively": (
        "baec_app.application.account_state",
        "from baec_app.domain import enums\nk = getattr(enums, 'TransitionRejectionKind').SAME_STATE",
        rule_rejection_kind_usage,
    ),
    "interface imports the data layer": ("baec_app.interfaces.ui", "from baec_app.data.repository import Repository", rule_r3_interfaces_do_not_import_data),
    "interface imports the data layer via alias": ("baec_app.interfaces.mcp.tools", "import baec_app.data.database as db", rule_r3_interfaces_do_not_import_data),
    "interface imports the data layer relatively": ("baec_app.interfaces.ui", "from ..data import repository", rule_r3_interfaces_do_not_import_data),
    "interface imports the data layer dynamically": (
        "baec_app.interfaces.ui",
        "import importlib\nimportlib.import_module('baec_app.data.repository')",
        rule_r3_interfaces_do_not_import_data,
    ),
    "production imports anthropic": ("baec_app.application.facades", "import anthropic", rule_no_model_or_mcp_integration),
    "production imports an MCP server": ("baec_app.application.composition", "from mcp.server.fastmcp import FastMCP", rule_no_model_or_mcp_integration),
    "application imports the MCP SDK": ("baec_app.application.facades", "from mcp.server import MCPServer", rule_no_model_or_mcp_integration),
    "domain imports the MCP SDK": ("baec_app.domain.models", "import mcp", rule_no_model_or_mcp_integration),
    "data imports the MCP SDK dynamically": (
        "baec_app.data.repository",
        "import importlib\nimportlib.import_module('mcp.server')",
        rule_no_model_or_mcp_integration,
    ),
    "MCP package imports a model SDK": ("baec_app.mcp.server", "import anthropic", rule_no_model_or_mcp_integration),
    "the AI service imports anthropic": ("baec_app.ai.service", "import anthropic", rule_no_model_or_mcp_integration),
    "a look-alike provider module imports anthropic": (
        "baec_app.ai.anthropic_provider_extra", "import anthropic", rule_no_model_or_mcp_integration),
    "the AI composition imports anthropic": (
        "baec_app.ai.composition", "from anthropic import Anthropic", rule_no_model_or_mcp_integration),
    "the application imports anthropic": ("baec_app.application.facades", "import anthropic", rule_no_model_or_mcp_integration),
    "the data layer imports anthropic": ("baec_app.data.ai_provenance", "import anthropic", rule_no_model_or_mcp_integration),
    "a script imports anthropic": ("scripts.seed_demo", "import anthropic", rule_no_model_or_mcp_integration),
    "the AI service imports anthropic dynamically": (
        "baec_app.ai.service", "import importlib\nimportlib.import_module('anthropic')", rule_no_model_or_mcp_integration),
    "the provider module imports another model SDK": (
        "baec_app.ai.anthropic_provider", "import openai", rule_no_model_or_mcp_integration),
    "the provider module imports the MCP SDK": (
        "baec_app.ai.anthropic_provider", "from mcp.server import MCPServer", rule_no_model_or_mcp_integration),
    "MCP package imports openai dynamically": (
        "baec_app.mcp.adapters",
        "import importlib\nimportlib.import_module('openai')",
        rule_no_model_or_mcp_integration,
    ),
    "a look-alike package name is not the MCP package": ("baec_app.mcpx.server", "from mcp.server import MCPServer", rule_no_model_or_mcp_integration),
    "production imports a model client dynamically": (
        "baec_app.application.proposals",
        "import importlib\nimportlib.import_module('anthropic')",
        rule_no_model_or_mcp_integration,
    ),
    "a proposal parser is defined": ("baec_app.application.proposals", "def parse_proposal(kind, data):\n    pass", rule_no_proposal_parser),
    "a method takes an authorization": (
        "baec_app.application.facades",
        "class F:\n    def confirm(self, approval, authorization=None):\n        pass",
        rule_no_authority_parameters,
    ),
    "a function takes an approved flag": ("baec_app.application.dormancy", "def record(approval, *, approved=True):\n    pass", rule_no_authority_parameters),
    "an interface takes authorized_by": ("baec_app.interfaces.ui", "def submit(authorized_by):\n    pass", rule_no_authority_parameters),
    "data imports application": ("baec_app.data.x", "import baec_app.application.requests", rule_r6_layering),
    "data imports application relatively": ("baec_app.data.x", "from ..application.errors import ApplicationError", rule_r6_layering),
    "requests imports data": ("baec_app.application.requests", "from baec_app.data.repository import Repository", rule_r7_data_free),
    "context imports data relatively": ("baec_app.application.context", "from ..data import database", rule_r7_data_free),
    "canonical calls asdict": ("baec_app.application.canonical", "import dataclasses\ndataclasses.asdict(x)", rule_canonical_purity),
    "canonical calls aliased asdict": (
        "baec_app.application.canonical",
        "from dataclasses import asdict as flatten\nflatten(x)",
        rule_canonical_purity,
    ),
    "canonical calls fields": ("baec_app.application.canonical", "from dataclasses import fields\nfields(x)", rule_canonical_purity),
    "canonical calls repr": ("baec_app.application.canonical", "repr(x)", rule_canonical_purity),
    "canonical calls vars": ("baec_app.application.canonical", "vars(x)", rule_canonical_purity),
    "canonical imports pickle": ("baec_app.application.canonical", "import pickle", rule_canonical_purity),
    "canonical reads __dict__": ("baec_app.application.canonical", "x.__dict__", rule_canonical_purity),
}


@pytest.mark.parametrize("case", VIOLATIONS.values(), ids=VIOLATIONS.keys())
def test_scanner_reports_synthetic_violations(case):
    module, source, rule = case
    assert rule(scan(source, module)) != []


ALLOWED = {
    "data imports domain": ("baec_app.data.x", "from baec_app.domain.models import Account", rule_r6_layering),
    "application imports data outside the foundation modules": (
        "baec_app.application.dormancy",
        "from baec_app.data.repository import Repository",
        rule_r7_data_free,
    ),
    "canonical uses getattr and str": ("baec_app.application.canonical", "getattr(x, 'a')\nstr(d)", rule_canonical_purity),
    "a string merely mentioning data": ("baec_app.domain.x", "'the data layer'", rule_r6_layering),
    "a docstring naming baec_app.data": (
        "baec_app.domain.x",
        '"""Stored by baec_app.data.repository; see baec_app.data."""\nX = "baec_app.data.database"',
        rule_r6_layering,
    ),
    "a docstring naming baec_app.data in a foundation module": (
        "baec_app.application.requests",
        '"""baec_app.data.repository.Repository executes these requests later."""',
        rule_r7_data_free,
    ),
    "a string passed to an unrelated call": ("baec_app.domain.x", "print('baec_app.data')", rule_r6_layering),
    "approval.py uses its own sentinel": (GATE_MODULE, "_GATE_KEY = object()\nHumanApproval(*f, _key=_GATE_KEY)", rule_r4_gate_key),
    "approval.py constructs approvals": (GATE_MODULE, "HumanApproval(*f, _key=_GATE_KEY)", rule_r4_human_approval_construction),
    "isinstance and annotations of HumanApproval": (
        "baec_app.application.dormancy",
        "from baec_app.application.approval import HumanApproval\ndef f(a: HumanApproval) -> bool:\n    return isinstance(a, HumanApproval)",
        rule_r4_human_approval_construction,
    ),
    "authority.py constructs HumanAuthorization": (
        AUTHORITY_MODULE,
        "from baec_app.domain.models import HumanAuthorization\nHumanAuthorization('u', t, a, 's')",
        rule_r1_authorization_construction,
    ),
    "repository rehydrates HumanAuthorization": (
        "baec_app.data.repository",
        "from baec_app.domain.models import HumanAuthorization\nHumanAuthorization('u', t, a, 's')",
        rule_r1_authorization_construction,
    ),
    "HumanAuthorization used as a type only": (
        "baec_app.application.dormancy",
        "def f(a: 'HumanAuthorization') -> None:\n    return isinstance(a, object)",
        rule_r1_authorization_construction,
    ),
    "HumanAuthorization imported under TYPE_CHECKING": (
        "baec_app.application.classification",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from baec_app.domain.models import HumanAuthorization",
        rule_r2_runtime_authorization_import,
    ),
    "HumanAuthorization imported under typing.TYPE_CHECKING": (
        "baec_app.application.dormancy",
        "import typing\nif typing.TYPE_CHECKING:\n    from baec_app.domain.models import HumanAuthorization",
        rule_r2_runtime_authorization_import,
    ),
    "authority.py imports HumanAuthorization": (
        AUTHORITY_MODULE,
        "from baec_app.domain.models import HumanAuthorization",
        rule_r2_runtime_authorization_import,
    ),
    "domain imports HumanAuthorization (R2 is application-only)": (
        "baec_app.domain.state_machine",
        "from baec_app.domain.models import HumanAuthorization",
        rule_r2_runtime_authorization_import,
    ),
    "classification imports authority": ("baec_app.application.classification", "from baec_app.application import authority", rule_r5_authority_importers),
    "dormancy imports authority": ("baec_app.application.dormancy", "from .authority import authorize", rule_r5_authority_importers),
    "account_state imports authority": ("baec_app.application.account_state", "from baec_app.application import authority", rule_r5_authority_importers),
    "application reads AUTHORIZATION_MISSING": (
        "baec_app.application.account_state",
        "from baec_app.domain.enums import TransitionRejectionKind as K\nONLY = (K.AUTHORIZATION_MISSING,)",
        rule_rejection_kind_usage,
    ),
    "a docstring mentioning SAME_STATE": (
        "baec_app.application.account_state",
        '"""SAME_STATE, BAEC_NOT_CURRENT and REASON_MISSING are decided by the locked state machine."""',
        rule_rejection_kind_usage,
    ),
    "an ordinary string naming a rejection": (
        "baec_app.application.account_state",
        "message = 'SAME_STATE'\nlog('the domain returned ADDRESSABILITY_NO')",
        rule_rejection_kind_usage,
    ),
    ".value compared with AUTHORIZATION_MISSING": (
        "baec_app.application.account_state",
        "ok = k.value == 'AUTHORIZATION_MISSING'",
        rule_rejection_kind_usage,
    ),
    ".value compared with an unrelated string": ("baec_app.application.account_state", "ok = origin.value == 'HUMAN_DRAFT'", rule_rejection_kind_usage),
    "rejection string compared without .value": ("baec_app.application.account_state", "ok = name == 'SAME_STATE'", rule_rejection_kind_usage),
    "logging a rejection name": (
        "baec_app.application.account_state",
        "logger.info('preview returned %s', 'ADDRESSABILITY_NO')",
        rule_rejection_kind_usage,
    ),
    "getattr by string on an unrelated object": (
        "baec_app.application.account_state",
        "x = getattr(result, 'SAME_STATE', None)",
        rule_rejection_kind_usage,
    ),
    "domain may use every rejection member": (
        "baec_app.domain.state_machine",
        "from .enums import TransitionRejectionKind as _K\nx = _K.SAME_STATE",
        rule_rejection_kind_usage,
    ),
    "interface imports application and domain": (
        "baec_app.interfaces.ui",
        "from baec_app.application import build_command_facade\nfrom baec_app.domain.models import BaecCandidate",
        rule_r3_interfaces_do_not_import_data,
    ),
    "application imports the data layer (R3 is interfaces-only)": (
        "baec_app.application.composition",
        "from baec_app.data.repository import Repository",
        rule_r3_interfaces_do_not_import_data,
    ),
    "the MCP interface package imports the MCP SDK": ("baec_app.mcp.server", "from mcp.server import MCPServer", rule_no_model_or_mcp_integration),
    "the exact Anthropic provider module imports anthropic": (
        "baec_app.ai.anthropic_provider", "import anthropic\nfrom anthropic import Anthropic", rule_no_model_or_mcp_integration),
    "a docstring mentioning anthropic and MCP": (
        "baec_app.application.facades",
        '"""Not connected to Claude (anthropic) or MCP in Phase 4."""',
        rule_no_model_or_mcp_integration,
    ),
    "a docstring mentioning parse_proposal": ("baec_app.application.proposals", '"""There is no parse_proposal in Phase 4."""', rule_no_proposal_parser),
    "a command takes only an approval": ("baec_app.application.facades", "def confirm_baec(self, approval):\n    pass", rule_no_authority_parameters),
    "a docstring mentioning authorization": (
        "baec_app.application.facades",
        'def f(self, session):\n    """Builds the authorization; approved=True has no meaning here."""',
        rule_no_authority_parameters,
    ),
    "a private helper is not public API": ("baec_app.application.authority", "def _build(authorization):\n    pass", rule_no_authority_parameters),
    "a docstring mentioning the gate key": (
        "baec_app.application.dormancy",
        '"""The _GATE_KEY stays private to approval.py."""',
        rule_r4_gate_key,
    ),
}


@pytest.mark.parametrize("case", ALLOWED.values(), ids=ALLOWED.keys())
def test_scanner_does_not_report_allowed_code(case):
    module, source, rule = case
    assert rule(scan(source, module)) == []


def test_relative_imports_resolve_against_the_package():
    assert _resolve_relative("baec_app.domain.models", False, 2, "data") == "baec_app.data"
    assert _resolve_relative("baec_app.domain", True, 1, "models") == "baec_app.domain.models"
    assert _resolve_relative("baec_app.application.requests", False, 1, "canonical") == "baec_app.application.canonical"
