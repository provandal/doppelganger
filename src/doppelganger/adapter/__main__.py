"""Run the Doppelgänger Adapter MCP server on stdio.

    python -m doppelganger.adapter

The server reads MCP messages from stdin and writes responses to stdout,
which is the wire protocol HarnessIT (and any MCP-compatible client)
uses to invoke tools.
"""

from doppelganger.adapter.server import build_server


def main() -> None:
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
