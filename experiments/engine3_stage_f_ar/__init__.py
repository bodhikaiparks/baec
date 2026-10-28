"""BAEC Engine 3 Stage F-AR: autonomous-reference track (offline, not executed).

A parallel track beside the locked human-reference Stage F design, which it does
not modify or supersede. Blinded autonomous reviewers annotate the same packets a
human would see. Their consensus or adjudicated annotation is an autonomous
reference: never a human reference, human ground truth, or evidence of human
semantic reliability.

This package contains no model client, network access, credential read, file
I/O, persistence, or seller action. It reuses the locked Stage F harness
unchanged. Every rule here is an IMPLEMENTATION rule, not a research finding.
"""
