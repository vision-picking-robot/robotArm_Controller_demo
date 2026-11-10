import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from .contracts import PickRequest
from .controller import Controller, ROOT
from .store import DomainError


def create_app(controller=None):
    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.controller.close()

    app = FastAPI(title='E0509 vision picking demo', lifespan=lifespan)
    if controller is None:
        path = Path(os.getenv('PICKING_CONFIG', str(ROOT / 'config/integration.json')))
        config = json.loads(path.read_text(encoding='utf-8'))
        if 'MOCK_MODE' in os.environ:
            value = os.environ['MOCK_MODE'].lower()
            if value not in ('true', 'false'): raise ValueError('MOCK_MODE must be true or false')
            config['mock_mode'] = value == 'true'
        controller = Controller(config)
    app.state.controller = controller

    @app.exception_handler(DomainError)
    async def domain(request, error):
        return JSONResponse({'detail': str(error)}, status_code=error.status)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'detail': str(error)}, status_code=422)

    @app.get('/healthz')
    def health():
        return dict(service='e0509-demo', mock_mode=controller.mock_mode, busy=controller.lock.locked())

    @app.post('/api/v1/plan')
    def plan(body: PickRequest):
        return controller.plan(body)

    @app.get('/api/v1/context')
    def context():
        calibration = controller.config.get('calibration') or {}
        return dict(camera=calibration.get('camera'),
                    calibration_revision=calibration.get('revision'), mock_mode=controller.mock_mode)

    @app.post('/api/v1/pick')
    def pick(body: PickRequest):
        return controller.move_to_pick(body)

    @app.post('/api/v1/move-target')
    def move_target(body: PickRequest):
        return controller.move_to_pick(body, target_only=True)

    @app.get('/api/v1/jobs/{identifier}')
    def status(identifier: str):
        return controller.status(identifier)

    return app
