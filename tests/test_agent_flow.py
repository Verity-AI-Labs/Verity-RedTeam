"""Queue mechanics tested with a fake worker; no model, network, or real repo edits."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('agent_flow', Path(__file__).parents[1] / 'scripts/agent_flow.py')
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        git('init')
        git('config', 'user.name', 'Workflow test')
        git('config', 'user.email', 'workflow@example.invalid')
        (self.root / 'PROGRESS.md').write_text('Measured state\n')
        git('add', 'PROGRESS.md')
        git('commit', '-m', 'fixture')
        (self.root / 'workflow').mkdir()
        self.board = self.root / 'workflow/tasks.json'
        self.task = {'id':'draft','owner':'codex','status':'ready','title':'Draft', 'brief':'Write it',
                     'scope':['draft.md','HANDOFF.md'],'deliverables':['draft.md','HANDOFF.md'],
                     'acceptance':'Both files exist','depends':[]}
        self.board.write_text(json.dumps({'tasks':[self.task]}))
        for name, value in [('ROOT',self.root),('BOARD',self.board),('RUNTIME',self.root/'.agent-flow'),('TREES',self.root/'.trees')]:
            context=patch.object(flow,name,value);context.start();self.addCleanup(context.stop)

    def test_dependencies_require_done_not_just_successful_exit(self):
        board={'tasks':[{'id':'core','status':'review'},dict(self.task,depends=['core'])]}
        self.assertFalse(flow.eligible(board['tasks'][1],board))
        board['tasks'][0]['status']='done'
        self.assertTrue(flow.eligible(board['tasks'][1],board))

    def test_preparing_does_not_touch_uncommitted_implementation(self):
        (self.root/'PROGRESS.md').write_text('Claude is editing this\n')
        task=flow.prepare('draft')
        self.assertEqual((self.root/'PROGRESS.md').read_text(),'Claude is editing this\n')
        self.assertEqual((Path(task['worktree'])/'PROGRESS.md').read_text(),'Measured state\n')
        self.assertIn('Claude is editing this',Path(task['packet']).read_text())

    def test_fake_worker_goes_to_review_and_keeps_output(self):
        code="from pathlib import Path; Path('draft.md').write_text('evidence'); Path('HANDOFF.md').write_text('done')"
        with patch.object(flow,'command',return_value=[sys.executable,'-c',code]):
            self.assertTrue(flow.run('draft','codex',1,2,True))
        task=json.loads(self.board.read_text())['tasks'][0]
        self.assertEqual(task['status'],'review')
        self.assertFalse((self.root/'draft.md').exists())
        self.assertTrue((Path(task['worktree'])/'draft.md').exists())

    def test_out_of_scope_write_blocks_and_is_never_merged(self):
        code="from pathlib import Path; Path('PROGRESS.md').write_text('bad'); Path('draft.md').write_text('x'); Path('HANDOFF.md').write_text('x')"
        with patch.object(flow,'command',return_value=[sys.executable,'-c',code]):
            self.assertFalse(flow.run('draft','codex',1,2,True))
        task=json.loads(self.board.read_text())['tasks'][0]
        self.assertEqual(task['status'],'blocked')
        self.assertIn('outside assigned scope',task['note'])
        self.assertEqual((self.root/'PROGRESS.md').read_text(),'Measured state\n')

    def test_failed_worker_is_blocked_without_retry(self):
        with patch.object(flow,'command',return_value=[sys.executable,'-c','raise SystemExit(7)']):
            self.assertFalse(flow.run('draft','codex',1,2,True))
        task=json.loads(self.board.read_text())['tasks'][0]
        self.assertEqual(task['status'],'blocked')
        self.assertIn('exited 7',task['note'])

    def test_external_owner_cannot_be_restarted(self):
        self.task['status']='external'
        self.board.write_text(json.dumps({'tasks':[self.task]}))
        with self.assertRaises(ValueError):
            flow.prepare('draft')


if __name__=='__main__':
    unittest.main()
