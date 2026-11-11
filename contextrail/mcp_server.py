"""Optional official MCP SDK adapter. Importing core never requires the SDK."""

import sqlite3

from .capabilities import HostCapabilities
from .errors import InvalidRequest, RailError
from .host import ContextTools
from .models import canonical
from .project import CONTEXT_DEFAULTS


async def run_stdio(tools: ContextTools):
    try:
        from mcp import types
        from mcp.server.lowlevel import NotificationOptions, Server
        from mcp.server.models import InitializationOptions
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise InvalidRequest("Install the optional MCP adapter: pip install 'contextrail[mcp]'.") from None
    sdk_v2 = not hasattr(Server, "list_tools")
    server = None
    from uuid import uuid4
    generation=uuid4().hex
    capabilities = HostCapabilities.read_only()
    project = getattr(tools, "config_view", {})
    default_packet_budget = min(project.get("context_bytes", CONTEXT_DEFAULTS["context_bytes"]), tools.max_read_bytes)

    async def list_tools():
        definitions = ContextTools.definitions() + [{"name": "context.capabilities",
            "description": "Report trusted adapter capabilities and explicit unknown host controls.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}}]
        definitions.append({"name": "context.packet", "description": "Read a source-checked Bridge task packet at an exact snapshot. No refresh or write access.",
            "input_schema": {"type": "object", "properties": {"snapshot": {"type": "string", "minLength": 1},
                "budget": {"type": "integer", "minimum": 1, "maximum": tools.max_read_bytes},
                "prefer_frequent_reads": {"type": "boolean"}},
                "required": ["snapshot"], "additionalProperties": False}})
        result=[]
        for item in definitions:
            annotations=types.ToolAnnotations(**({'read_only_hint':True,'destructive_hint':False,'idempotent_hint':True,'open_world_hint':False}
                if sdk_v2 else {'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}))
            result.append(types.Tool(name=item['name'],description=item['description'],annotations=annotations,
                **({'input_schema':item['input_schema']} if sdk_v2 else {'inputSchema':item['input_schema']})))
        return result


    # ContextTools validates every field and produces our stable error envelope;
    # the SDK's default validator returns free-form, non-JSON errors instead.
    async def call_tool(name, arguments, request_id):
        try:
            def invoke():
                if name=='context.capabilities':
                    tools.store.session(tools.scope,tools.session_id)
                    if arguments!={}:raise InvalidRequest('Capability reporting accepts no arguments.')
                    return capabilities.describe()
                if name=='context.packet':
                    from .bridge import BridgeEvidence
                    from .models import integer
                    tools._keys(arguments,required={'snapshot'},optional={'budget','prefer_frequent_reads'})
                    budget=arguments.get('budget',default_packet_budget);integer(budget,1)
                    if budget>tools.max_read_bytes:raise InvalidRequest('Packet context budget exceeds the bound reader limit.')
                    return BridgeEvidence(tools.store,tools.scope,tools.session_id).packet(
                        arguments['snapshot'], budget=budget,
                        prefer_frequent_reads=arguments.get('prefer_frequent_reads', False))
                return tools.call(name,arguments)
            from .accounting import HostAccounting
            if name not in {'context.metrics','context.efficiency','context.read_heat'}:
                result=HostAccounting(tools.store,tools.scope,tools.session_id).call_tool(tools,name,arguments,
                    observation_id='mcp-'+generation+'-'+str(request_id),invoke=invoke)
            else:result=invoke()
            return types.CallToolResult(content=[types.TextContent(type='text',text=canonical(result))])
        except RailError as exc:
            error = {"code": exc.code, "message": str(exc)}
        except sqlite3.Error:
            error = {"code": "storage_error", "message": "Local storage is unavailable."}
        return types.CallToolResult(content=[types.TextContent(type="text", text=canonical({"error": error}))], **({"is_error":True} if sdk_v2 else {"isError":True}))

    if sdk_v2:
        async def on_list_tools(ctx,params):
            return types.ListToolsResult(tools=await list_tools())
        async def on_call_tool(ctx,params):
            return await call_tool(params.name,params.arguments or {},ctx.request_id)
        server=Server('contextrail',version='0.1.0',on_list_tools=on_list_tools,on_call_tool=on_call_tool)
        initialization=server.create_initialization_options()
    else:
        server=Server('contextrail')
        server.list_tools()(list_tools)
        async def legacy_call(name,arguments):
            return await call_tool(name,arguments,server.request_context.request_id)
        server.call_tool(validate_input=False)(legacy_call)
        initialization=InitializationOptions(server_name='contextrail',server_version='0.1.0',
            capabilities=server.get_capabilities(NotificationOptions(),{}))
    async with stdio_server() as (read, write):
        await server.run(read,write,initialization)
