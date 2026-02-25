"""Entry point for the Wattson dev MCP server.

Run with:  PYTHONPATH=scripts python -m mcp_server
"""

from fastmcp import FastMCP

from mcp_server.tools import register_all

mcp = FastMCP("Wattson Dev")
register_all(mcp)

if __name__ == "__main__":
    mcp.run()
