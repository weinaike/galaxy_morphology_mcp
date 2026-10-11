"""Beam-search mechanisation package (state graph, signatures, surveyor loop).

Kept import-light: heavy modules (networkx, pydantic) are imported lazily by
their consumers so that ``import beam`` stays cheap for tool registration.
"""
