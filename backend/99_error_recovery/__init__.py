"""Error recovery component registry.

Runtime-owned sandbox recovery components.  These are called by the one-dragon
pipeline when official/site errors are detected; operators should not rely on
manual browser restarts for known cases such as Cloudflare 1015 or HTTP 429.
"""
