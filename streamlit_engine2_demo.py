"""Entry point for the BAEC Engine 2 public demo. Behavior lives in baec_app.engine2.public_demo.page; each visitor's
synthetic state comes from scripts/engine2_public_demo_seed.py.

    streamlit run streamlit_engine2_demo.py
"""

from baec_app.engine2.public_demo.page import main
from scripts.engine2_public_demo_seed import new_visitor_seed

main(new_visitor_seed)
