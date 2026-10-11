"""External provider adapters (CLAUDE.md non-negotiable).

Every integration with an external service lives behind an interface in this
package so the rest of the code depends on a seam, never on a vendor SDK. The
only subpackage today is ``app.providers.llm`` (the benchmark answerer/judge
providers used by M2); its tests exercise a deterministic fake and never touch
the network.
"""
