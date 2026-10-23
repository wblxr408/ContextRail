"""Optional official MCP SDK adapter. Importing core never requires the SDK."""

import sqlite3

from .capabilities import HostCapabilities
from .errors import InvalidRequest, RailError
from .host import ContextTools
from .models import canonical


async def run_stdio(tools: ContextTools):
    try:
        from mcp import types
        from mcp.server.lowlevel import NotificationOptions, Server
        from mcp.server.models import InitializationOptions
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise InvalidRequest("Install the optional MCP adapter: pip install 'contextrail[mcp]'.") from None
    server = Server("contextrail")
    from uuid import uuid4
    generation=uuid4().hex
    capabilities = HostCapabilities.read_only()

    @server.list_tools()
    async def list_tools():
        definitions = ContextTools.definitions() + [{"name": "context.capabilities",
            "description": "Report trusted adapter capabilities and explicit unknown host controls.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}}]
        definitions.append({"name": "context.packet", "description": "Read a source-checked Bridge task packet at an exact snapshot. No refresh or write access.",
            "input_schema": {"type": "object", "properties": {"snapshot": {"type": "string", "minLength": 1},
                "budget": {"type": "integer", "minimum": 1, "maximum": tools.max_read_bytes}},
                "required": ["snapshot"], "additionalProperties": False}})
        return [types.Tool(name=item["name"], description=item["description"], inputSchema=item["input_schema"],
                           annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                             idempotentHint=True, openWorldHint=False))
                for item in definitions]

    # ContextTools validates every field and produces our stable error envelope;
    # the SDK's default validator returns free-form, non-JSON errors instead.
    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            def invoke():
                if name=='context.capabilities':
                    tools.store.session(tools.scope,tools.session_id)
                    if arguments!={}:raise InvalidRequest('Capability reporting accepts no arguments.')
                    return capabilities.describe()
                if name=='context.packet':
                    from .bridge import BridgeEvidence
                    from .models import integer
                    tools._keys(arguments,required={'snapshot'},optional={'budget'})
                    budget=arguments.get('budget',min(8192,tools.max_read_bytes));integer(budget,1)
                    if budget>tools.max_read_bytes:raise InvalidRequest('Packet context budget exceeds the bound reader limit.')
                    return BridgeEvidence(tools.store,tools.scope,tools.session_id).packet(arguments['snapshot'],budget=budget)
                return tools.call(name,arguments)
            from .accounting import HostAccounting
            if name not in {'context.metrics','context.efficiency'}:
                result=HostAccounting(tools.store,tools.scope,tools.session_id).call_tool(tools,name,arguments,
                    observation_id='mcp-'+generation+'-'+str(server.request_context.request_id),invoke=invoke)
            else:result=invoke()
            return types.CallToolResult(content=[types.TextContent(type='text',text=canonical(result))])
        except RailError as exc:
            error = {"code": exc.code, "message": str(exc)}
        except sqlite3.Error:
            error = {"code": "storage_error", "message": "Local storage is unavailable."}
        return types.CallToolResult(content=[types.TextContent(type="text", text=canonical({"error": error}))], isError=True)

    async with stdio_server() as (read, write):
        await server.run(read, write, InitializationOptions(
            server_name="contextrail", server_version="0.1.0",
            capabilities=server.get_capabilities(NotificationOptions(), {})))
