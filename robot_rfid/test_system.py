import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import sys
import types

import numpy as np
from fastapi.testclient import TestClient

try:
    from .api_server import create_app
    from .database import normalize_uid
    from .orchestrator import Orchestrator, SimRobot, SimScanner, SimVision, State
    from .rfid_scanner import RFIDScanner, GET_UID
    from .vision import Calibration, Detection
except ImportError:
    from api_server import create_app
    from database import normalize_uid
    from orchestrator import Orchestrator, SimRobot, SimScanner, SimVision, State
    from rfid_scanner import RFIDScanner, GET_UID
    from vision import Calibration, Detection


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name) / 'test.db')
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def test_uid_validation(self):
        self.assertEqual(normalize_uid('00:00:00:a1'), '000000A1')
        self.assertEqual(normalize_uid('04:00:01:02:03:04:05'), '04000102030405')
        for value in ('A1', '00::00:00:A1', '0000000G', '0B71E55D00'):
            with self.assertRaises(ValueError): normalize_uid(value)

    def test_api_idempotency_queue_and_websocket(self):
        with self.client.websocket_connect('/ws') as ws:
            self.assertIsNone(ws.receive_json()['task'])
            body = dict(uid='0B:71:E5:5D', quantity=2, event_id='one')
            response = self.client.post('/api/v1/scan-event', json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(ws.receive_json()['product']['yolo_class_name'], 'snack_pepero')
            self.assertFalse(self.client.post('/api/v1/scan-event', json=body).json()['created'])
            self.assertEqual(self.client.post('/api/v1/scan-event', json={**body,'quantity':3}).status_code, 409)
            self.assertEqual(self.client.post('/api/v1/scan-event', json={**body,'event_id':'two'}).status_code, 409)
            task = self.client.get('/api/v1/current-task').json()['task']
            path = '/api/v1/tasks/' + task['task_id']
            self.assertEqual(self.client.patch(path, json=dict(completed=1,status='COMPLETED')).status_code, 409)
            self.assertEqual(self.client.patch(path, json=dict(completed=2,status='COMPLETED')).status_code, 200)
            self.assertEqual(ws.receive_json()['status'], 'COMPLETED')
            self.assertIsNone(self.client.get('/api/v1/current-task').json()['task'])
        self.assertEqual(self.client.get('/api/v1/tags/000000FF').status_code,404)
        self.assertEqual(self.client.get('/api/v1/tags/bad').status_code,422)
        self.assertEqual(self.client.post('/api/v1/scan-event',json={'uid':'000000FF'}).status_code,404)

    def api_adapter(self):
        client = self.client
        class API:
            def enqueue(self, uid, quantity, event_id):
                response = client.post('/api/v1/scan-event',json=dict(uid=uid,quantity=quantity,event_id=event_id))
                response.raise_for_status()
                return response.json()
            def current(self): return client.get('/api/v1/current-task').json()['task']
            def progress(self, task_id, completed, status, error=None):
                response = client.patch('/api/v1/tasks/'+task_id,json=dict(completed=completed,status=status,error=error))
                response.raise_for_status()
        return API()

    def test_orchestrator_full_loop(self):
        robot = Mock(wraps=SimRobot())
        api = self.api_adapter()
        api.enqueue('0B71E75D', 1, 'queued')
        controller = Orchestrator(api, robot, SimScanner(), SimVision())
        trigger = Mock()
        controller.run_box(trigger, 3)
        trigger.assert_called_once()
        self.assertEqual(robot.grip.call_count,4)
        self.assertEqual(robot.place.call_count,4)
        robot.home.assert_called_once()
        self.assertEqual(controller.state,State.IDLE)
        self.assertIsNone(api.current())

    def test_motion_failure_stops_without_home(self):
        robot = Mock(wraps=SimRobot())
        robot.grip.side_effect = RuntimeError('Grip sensor failed')
        controller = Orchestrator(self.api_adapter(), robot, SimScanner(), SimVision())
        with self.assertRaises(RuntimeError): controller.run_box(lambda:None,2)
        robot.stop.assert_called_once()
        robot.home.assert_not_called()
        robot.place.assert_not_called()
        self.assertEqual(self.app.state.database.product('0B71E55D')['status'],'DEFECTIVE')

    def test_calibration_depth_and_bounds(self):
        cal = Calibration([[100,0,50],[0,100,50],[0,0,1]],np.eye(4),[-1,-1,0],[1,1,2],[0,0,0])
        self.assertEqual(cal.pose(Detection(50,50,1,0.9)),(0,0,1,0,0,0))
        for detection in (Detection(50,50,0,1),Detection(500,50,1,1),Detection(50,50,float('nan'),1)):
            with self.assertRaises(ValueError): cal.pose(detection)

    def test_pcsc_retry_and_status(self):
        class NoCard(Exception): pass
        class ConnectionFailure(Exception): pass
        connection = Mock()
        connection.connect.side_effect = [ConnectionFailure(), None]
        connection.transmit.return_value = ([11,113,229,93],0x90,0)
        reader = Mock()
        reader.__str__ = Mock(return_value='ACS ACR1555 Virtual PC/SC')
        reader.createConnection.return_value = connection
        modules = {'smartcard':types.ModuleType('smartcard'),
                   'smartcard.System':types.SimpleNamespace(readers=lambda:[reader]),
                   'smartcard.Exceptions':types.SimpleNamespace(NoCardException=NoCard,CardConnectionException=ConnectionFailure)}
        stop = Mock()
        stop.is_set.return_value = False
        with patch.dict(sys.modules,modules):
            self.assertEqual(RFIDScanner().scan(stop=stop),'0B71E55D')
            connection.transmit.assert_called_with(GET_UID)
            self.assertEqual(connection.disconnect.call_count,2)
            connection.connect.side_effect = None
            connection.transmit.return_value = ([],0x6A,0x81)
            with self.assertRaises(RuntimeError): RFIDScanner().scan(stop=stop)


if __name__ == '__main__':
    unittest.main()
