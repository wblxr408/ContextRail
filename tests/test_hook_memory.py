"""Current memory optimization contract; no prior suites are selected."""
import os
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from tests.test_claude_mem_compact import CompactAssistance


class HookMemory(unittest.TestCase):
    def setUp(self):
        self.f=CompactAssistance();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.prepare()

    def test_no_pending_task_submission_and_post_tool_do_not_require_optional_node(self):
        with patch.dict(os.environ,{'CONTEXTRAIL_NODE':str(self.f.root/'unavailable-optional-node')}):
            task=self.f.event('codex','UserPromptSubmit',prompt='Continue the exact task without optional history')
            text=task.get('hookSpecificOutput',{}).get('additionalContext','')
            self.assertIn(self.f.quote,text,task)
            self.assertEqual(self.f.event('codex','PostToolUse',tool_name='mcp__contextrail__context_status',tool_input={}),{})

    def test_existing_marker_without_pending_audit_still_returns_exact_recovery_once(self):
        from contextrail.native_assistance import marker
        registry=json.loads(self.f.registry.read_bytes())
        binding=next(b for b in registry['bindings'] if b['client']=='codex')
        # A real upstream mark whose process died before writing Rail's audit.
        marker(binding,'codex','codex-native','mark')
        self.f.capture_compact('codex','orphan-marker-original-source')
        recovered=self.f.event('codex','PostToolUse',tool_name='Read',tool_input={'file_path':str(self.f.bundle/'user.md')})
        text=recovered.get('hookSpecificOutput',{}).get('additionalContext','')
        self.assertIn(self.f.quote,text,recovered)
        self.assertEqual(json.loads(text)['recovery']['source_event'],'orphan-marker-original-source')
        repeated=self.f.event('codex','PostToolUse',tool_name='Read',tool_input={'file_path':str(self.f.bundle/'user.md')})
        self.assertNotIn('hookSpecificOutput',repeated)


if __name__=='__main__':unittest.main()
