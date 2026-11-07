"""What an organisation's seed reads with its own tools: its public face and its own statements of direction. No
extraction here; the seed skill reads these and records each reading in a Seeds note (`source: <id>`, `read: <date>`),
which coverage counts (docs/image.md section 3)."""
from __future__ import annotations

from . import Source

ORG = ("enterprise",)
SOURCES = (
    Source(id="site", editions=ORG, runs="agent", where="public", rank=1, facets=("direction", "customers"),
           about="the organisation's own site: what it sells, to whom, and how it says so"),
    Source(id="writing", editions=ORG, runs="agent", where="public", rank=2, facets=("direction", "culture"),
           about="its blog, posts and published writing"),
    Source(id="founders", editions=ORG, runs="agent", where="public", rank=3, facets=("direction",),
           about="its founders and leaders on the record: talks, interviews, posts"),
    Source(id="press", editions=ORG, runs="agent", where="public", rank=4, facets=("direction", "customers"),
           about="what the press has written about it"),
    Source(id="jobs", editions=ORG, runs="agent", where="public", rank=5, facets=("direction", "operations"),
           about="the roles it is hiring for, which say where it is putting effort"),
    Source(id="customers", editions=ORG, runs="agent", where="public", rank=6, facets=("customers",),
           about="its customers in public: case studies, logos, reviews"),
    Source(id="market", editions=ORG, runs="agent", where="public", rank=7, facets=("customers", "direction"),
           about="its competitors, regulators and standards"),
    Source(id="goals", editions=ORG, runs="agent", where="internal", needs="docs", rank=1, facets=("direction",),
           about="its stated goals and plans of record"),
    Source(id="meetings", editions=ORG, runs="agent", where="internal", needs="docs", rank=2, facets=("direction", "operations"),
           about="leadership and team meeting logs: decisions taken, what was deferred, what keeps coming up"),
    Source(id="docs", editions=ORG, runs="agent", where="internal", needs="docs", rank=3, facets=("direction", "product", "engineering"),
           about="strategy, planning and process documents written for many people"),
    Source(id="chat", editions=ORG, runs="agent", where="internal", needs="chat", rank=5, facets=("culture", "operations"),
           about="its channels written for many people"),
    Source(id="crm", editions=ORG, runs="agent", where="internal", needs="crm", rank=7, facets=("customers",),
           about="its customer records and pipeline"),
)
