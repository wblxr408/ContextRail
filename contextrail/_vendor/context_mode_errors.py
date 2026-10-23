"""MODIFIED Python port of context-mode classifyError (ELv2).

Copyright (c) mksglu and context-mode contributors.
Source: 90af14e34bf35120c3310f7a75f54fa99d2cabba,
src/session/error-classifier.ts. See context_mode_LICENSE.txt.
Only ordered error buckets are ported; no command/duration classifier or I/O.
The caller must establish a source-declared failure before invoking this heuristic.
"""
import re

CATEGORIES={'file_not_found','command_not_found','edit_match_failed','test_failed',
            'syntax_error','runtime_error','permission_denied','git_conflict','timeout','unknown'}


def classify_error(message,tool=''):
    tool=tool if isinstance(tool,str) else ''
    msg=message.lower() if isinstance(message,str) else ''
    if any(s in msg for s in ('enoent','no such file','cannot find module','filenotfounderror')):return 'file_not_found'
    if 'command not found' in msg or ': not found' in msg or re.search(r'\bexit(?:\s+code)?\s*[:=]?\s*127\b',msg):return 'command_not_found'
    if (tool in {'Edit','MultiEdit'} and any(s in msg for s in ('old_string','could not find string','string to replace not found','matches multiple'))) or any(s in msg for s in ('old_string not found','string to replace not found')):return 'edit_match_failed'
    if 'conflict' in msg and any(s in msg for s in ('merge','rebase','git')) or msg.startswith('conflict') or 'merge conflict' in msg:return 'git_conflict'
    if any(s in msg for s in ('etimedout','timed out','timeout','deadline exceeded')):return 'timeout'
    if any(s in msg for s in ('eacces','permission denied','operation not permitted','eperm')):return 'permission_denied'
    if any(s in msg for s in ('syntaxerror','unexpected token','unexpected end of','parse error')) or re.search(r'\berror\s+ts\d{3,5}\b',msg):return 'syntax_error'
    if any(s in msg for s in ('test failed','tests failed','assertion')) or re.search(r'\bfail(?:ed)?\b.*\btest\b|\b\d+\s+tests?\s+failed\b|^fail\s|\nfail\s',msg):return 'test_failed'
    if any(s in msg for s in ('typeerror','referenceerror','rangeerror','uncaught exception','traceback (most recent call last)','nullpointerexception')):return 'runtime_error'
    return 'unknown'
