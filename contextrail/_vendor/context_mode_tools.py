"""MODIFIED: Python port of context-mode tool-name normalization.
Copyright 2026 Mert Koseoglu. Elastic License 2.0 (ELv2).
Source: src/session/extract.ts at 90af14e34bf35120c3310f7a75f54fa99d2cabba.
Local changes: Python mapping/function, retain original tool name separately.
Full license in context_mode_LICENSE.txt; see THIRD_PARTY_NOTICES.md.
"""
TOOL_NAME_NORMALIZE = {'run_shell_command': 'Bash', 'read_file': 'Read', 'read_many_files': 'Read', 'grep_search': 'Grep', 'search_file_content': 'Grep', 'web_fetch': 'WebFetch', 'write_file': 'Write', 'edit': 'Edit', 'glob': 'Glob', 'todo_write': 'TodoWrite', 'ask_user_question': 'AskUserQuestion', 'list_directory': 'LS', 'save_memory': 'Memory', 'skill': 'Skill', 'exit_plan_mode': 'ExitPlanMode', 'agent': 'Agent', 'bash': 'Bash', 'view': 'Read', 'grep': 'Grep', 'fetch': 'WebFetch', 'shell': 'Bash', 'shell_command': 'Bash', 'exec_command': 'Bash', 'container.exec': 'Bash', 'local_shell': 'Bash', 'grep_files': 'Grep', 'run_command': 'Bash', 'view_file': 'Read', 'read_url_content': 'WebFetch', 'list_dir': 'LS', 'search_web': 'WebSearch'}

def normalize_tool_name(name):
    return TOOL_NAME_NORMALIZE.get(name, name)
