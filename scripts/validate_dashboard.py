"""Headless MOCK dashboard regression check; never opens cameras or robot transports."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))


def main():
    import uvicorn
    from playwright.sync_api import sync_playwright
    from app.config import Settings
    from app.main import create_app
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', type=Path, default=ROOT / 'validation/dashboard')
    parser.add_argument('--reference', type=Path)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (ROOT / 'web/app/static').rglob('*') if p.is_file()}
    if args.reference:
        assert hashes == json.loads((args.reference / 'ui-baseline.json').read_text()), 'Static UI changed'
    with TemporaryDirectory() as directory:
        app = create_app(Settings(camera_autostart=False, voice_enabled=True), state_root=Path(directory))
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if time.monotonic() >= deadline: raise TimeoutError('Web startup timed out')
                time.sleep(.05)
            results = {}
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True)
                pages = []
                for width, height in [(1440,900), (390,844)]:
                    page = browser.new_page(viewport=dict(width=width,height=height))
                    errors = []
                    page.on('pageerror', lambda error, target=errors: target.append(str(error)))
                    page.goto(f'http://127.0.0.1:{port}/')
                    page.wait_for_selector('#dashboard-order .order-no')
                    page.evaluate('document.fonts.ready')
                    screenshot = args.artifacts / f'ui-after-{width}.png'
                    page.screenshot(path=str(screenshot), full_page=True, animations='disabled')
                    boxes = {key:page.locator(key).bounding_box() for key in ['#live-camera','#dashboard-order','#dashboard-events']}
                    buttons = page.locator('#dashboard-order button').count()
                    result = dict(errors=errors,boxes=boxes,buttons=buttons)
                    if args.reference:
                        before = json.loads((args.reference/'ui-before.json').read_text())[str(width)]
                        assert boxes == before['boxes'] and buttons == before['buttons'], 'Dashboard layout changed'
                        from PIL import Image, ImageChops
                        diff = ImageChops.difference(Image.open(args.reference/f'ui-before-{width}.png').convert('RGB'), Image.open(screenshot).convert('RGB'))
                        result['pixel_identical'] = diff.getbbox() is None
                        if diff.getbbox() is not None: diff.save(args.artifacts/f'ui-diff-{width}.png')
                        # Seeded activity entries contain the actual startup time.
                        from PIL import ImageDraw
                        import math
                        left = Image.open(args.reference/f'ui-before-{width}.png').convert('RGB')
                        right = Image.open(screenshot).convert('RGB')
                        for clock in page.locator('#dashboard-events time').all():
                            box = clock.bounding_box()
                            rect = (math.floor(box['x']),math.floor(box['y']),
                                    math.ceil(box['x']+box['width']),math.ceil(box['y']+box['height']))
                            ImageDraw.Draw(left).rectangle(rect,fill='black')
                            ImageDraw.Draw(right).rectangle(rect,fill='black')
                        result['pixel_identical_except_activity_times'] = ImageChops.difference(left,right).getbbox() is None
                        assert result['pixel_identical_except_activity_times'], 'Unexpected visual difference outside activity timestamps'
                    results[str(width)] = result
                    pages.append(page)
                # Capture both viewports before mutating the shared demo order/logs.
                for page in pages:
                    # The existing manual controls keep their original processing order.
                    page.locator('#dashboard-order [data-action="cmd-stop"]').click()
                    page.wait_for_function("document.querySelector('#dashboard-order').textContent.includes('피킹을 정지했습니다')")
                    page.locator('#dashboard-order [data-action="cmd-go"]').click()
                    page.wait_for_function("document.querySelector('#dashboard-order').textContent.includes('피킹을 재개합니다')")
                    page.locator('#dashboard-order [data-action="rfid-tag-0B71E55D"]').click()
                    page.wait_for_function("document.querySelector('#dashboard-order').textContent.includes('RFID [0B71E55D] 인식 완료')")
                    page.locator('[data-action="voice"]').first.click()
                    page.locator('#voice-panel').wait_for(state='visible')
                    page.close()
                assert not any(result['errors'] for result in results.values()), results
                browser.close()
            (args.artifacts/'dashboard-result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(results,ensure_ascii=False))
        finally:
            server.should_exit = True
            thread.join(10)
            sock.close()


if __name__ == '__main__':
    main()
