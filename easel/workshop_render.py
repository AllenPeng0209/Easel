"""Native local composition of frozen selected shot candidates."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import subprocess
import tempfile

from easel.video_workshop import WorkshopStore, WorkshopError, _digest, _now

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='workshop-render')


def submit_render(outputs, identifier, render_id):
    _POOL.submit(_render, Path(outputs), identifier, render_id)


def _render(outputs, identifier, render_id):
    store = WorkshopStore(outputs)
    def settle(**fields):
        with store._locked():
            project = store._read(identifier)
            receipt = store._render_entry(project, render_id)
            receipt.update(**fields, updated_at=_now())
            store._write(project)
            return dict(receipt)
    try:
        receipt = settle(status='running')
        directory = outputs / ('视频工坊-' + identifier[:8]) / 'renders'
        if directory.is_symlink() or directory.parent.is_symlink():
            raise WorkshopError('合成目录不能是符号链接', 403)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / (render_id + '.mp4')
        with tempfile.TemporaryDirectory(prefix='.compose-', dir=directory) as scratch:
            normalized = []
            size = None
            for index, item in enumerate(receipt['inputs']):
                source = store._media_path(item['path'])
                if _digest(source) != item['sha256']:
                    raise WorkshopError('已选镜头的内容发生变化', 409)
                if size is None:
                    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height',
                        '-select_streams', 'v:0', '-of', 'json', str(source)], check=True, capture_output=True, timeout=20)
                    first = json.loads(result.stdout)['streams'][0]
                    a, b = map(int, receipt['aspect_ratio'].split(':'))
                    short = min(first['width'], first['height'], 1080)
                    size = (round(short * a / b / 2) * 2, short) if a >= b else (short, round(short * b / a / 2) * 2)
                segment = Path(scratch) / f'{index:04d}.mp4'
                width, height = size
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source),
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-map', '0:v:0', '-map', '0:a:0?',
                    '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24',
                    '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000',
                    '-ac', '2', '-shortest', str(segment)], check=True, capture_output=True, timeout=180)
                normalized.append(segment)
            listing = Path(scratch) / 'clips.txt'
            listing.write_text(''.join("file '" + str(path).replace("'", "'\\''") + "'\n" for path in normalized))
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', '0',
                '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(destination)],
                check=True, capture_output=True, timeout=180)
        store._validate_video(destination)
        settle(status='succeeded', progress=1.0, progress_known=True, completed_at=_now(),
               output_path=destination.relative_to(outputs).as_posix(), output_sha256=_digest(destination))
    except Exception:
        settle(status='failed', completed_at=_now(), message='本机合成未完成，请检查镜头素材后重新合成')
