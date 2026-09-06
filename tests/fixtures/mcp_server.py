"""测试用 stdio MCP 服务。"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gateway-test")


@mcp.tool()
def get_magic_number() -> int:
    """Return the magic number known only to this MCP server."""
    return 4242


if __name__ == "__main__":
    mcp.run(transport="stdio")
