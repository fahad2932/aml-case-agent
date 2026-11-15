"""MCP server exposing the read-only bank tools, so any MCP client can use them.

Run:  python -m amlagent.mcp_server      (stdio transport)
"""
from mcp.server.fastmcp import FastMCP

from . import tools

mcp = FastMCP("aml-bank-data")
for fn in tools.ALL_TOOLS:
    mcp.tool()(fn)

if __name__ == "__main__":
    mcp.run()
