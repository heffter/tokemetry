"""Integration harness: realistic proxy-exporter fixtures and a replay driver.

The fixtures (:mod:`fixtures`) are the shared truth for what an AI-provider
proxy exports for anthropic, openai, and zai -- cache tiers, streaming snapshot
sequences, retries, cross-provider fallback, client-cancelled and failed
attempts. The replay driver (:mod:`driver`) posts them through the real HTTP
ingest surface with the Python client, so this package doubles as the
end-to-end contract test for both the server and the client (Task 65.4).
"""
