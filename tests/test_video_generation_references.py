"""Actual reference files, immutable proof, and the pinned canvas edge contract."""


from __future__ import annotations


import hashlib


import importlib


import importlib.util


import json


from email.parser import BytesParser


from email.policy import default as email_policy


from pathlib import Path


import shutil


import subprocess


import httpx


from PIL import Image


import pytest


@pytest.fixture
def refs_module():
    if importlib.util.find_spec('easel.video_generation_references') is not None:
        return importlib.import_module('easel.video_generation_references')


@pytest.fixture
def media(tmp_path):
    outputs = tmp_path / 'workspace' / 'outputs'
    directory = outputs / 'family' / 'refs'
    directory.mkdir(parents=True)
    Image.new('RGB', (24, 32), 'red').save(directory / 'mother.png')
    Image.new('RGB', (24, 32), 'blue').save(directory / 'child.png')
    if not shutil.which('ffmpeg'):
        pytest.skip('actual AV fixtures require ffmpeg')
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=90x160:r=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=400:sample_rate=32000', '-t', '0.5',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart',
                    str(directory / 'voice.mp4')], check=True, capture_output=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=500:sample_rate=32000',
                    '-t', '0.5', str(directory / 'voice.wav')], check=True, capture_output=True)
    return outputs, directory


def test_freeze_uses_real_image_and_audio_video_evidence(refs_module, media):
    assert refs_module is not None, 'Host reference support is missing'
    outputs, directory = media
    result = refs_module.freeze_references(outputs, [
        {'kind': 'image', 'path': 'family/refs/mother.png'},
        {'kind': 'video', 'path': 'family/refs/voice.mp4'},
        {'kind': 'audio', 'path': 'family/refs/voice.wav'},
    ])
    image, video, audio = result
    assert image['sha256'] == hashlib.sha256((directory / 'mother.png').read_bytes()).hexdigest()
    assert (image['width'], image['height'], image['mimeType']) == (24, 32, 'image/png')
    assert video['hasAudio'] is True and (video['width'], video['height']) == (90, 160)
    assert 400 <= video['durationMs'] <= 600 and audio['hasAudio'] is True
    assert audio['mimeType'] == 'audio/wav' and 400 <= audio['durationMs'] <= 600
    assert refs_module.verify_references(outputs, result) == [directory / 'mother.png', directory / 'voice.mp4', directory / 'voice.wav']


@pytest.mark.parametrize('reference', [
    {'kind': 'image', 'path': '../foreign.png'}, {'kind': 'image', 'path': '/tmp/foreign.png'},
    {'kind': 'image', 'path': 'https://foreign.invalid/a.png'}, {'kind': 'image', 'path': 'family/./refs/mother.png'},
    {'kind': 'image', 'path': 'family\\refs\\mother.png'}, {'kind': 'image', 'path': '_inbox/private/mother.png'},
    {'kind': 'image', 'path': 'family/refs/mother.png', 'sha256': 'a' * 64},
    {'kind': 'script', 'path': 'family/refs/mother.png'},
])
def test_unowned_paths_and_caller_metadata_never_become_references(refs_module, media, reference):
    with pytest.raises(Exception):
        refs_module.freeze_references(media[0], [reference])


def test_symlink_components_and_changed_file_are_rejected(refs_module, media, tmp_path):
    outputs, directory = media
    original = refs_module.freeze_references(outputs, [{'kind': 'image', 'path': 'family/refs/mother.png'}])
    (directory / 'mother.png').write_bytes((directory / 'child.png').read_bytes())
    with pytest.raises(Exception):
        refs_module.verify_references(outputs, original)
    (directory / 'linked.png').symlink_to(directory / 'child.png')
    (outputs / 'linked-directory').symlink_to(directory, target_is_directory=True)
    for path in ('family/refs/linked.png', 'linked-directory/child.png'):
        with pytest.raises(Exception):
            refs_module.freeze_references(outputs, [{'kind': 'image', 'path': path}])


def test_media_kind_and_real_duration_are_not_taken_from_filename(refs_module, media):
    outputs, directory = media
    (directory / 'fake.png').write_text('not an image')
    (directory / 'fake.mp4').write_bytes((directory / 'mother.png').read_bytes())
    for ref in ({'kind': 'image', 'path': 'family/refs/fake.png'}, {'kind': 'video', 'path': 'family/refs/fake.mp4'},
                {'kind': 'audio', 'path': 'family/refs/mother.png'}):
        with pytest.raises(Exception):
            refs_module.freeze_references(outputs, [ref])


@pytest.mark.parametrize('model,seconds,ratio,refs', [
    ('easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step', 6, '9:16', []),
    ('easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step', 15, '9:16', [{'kind': 'image'}]),
    ('easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step', 6, '1:1', [{'kind': 'image'}]),
    ('easel-ppu-h3-local::minimax-h3-dense8', 6, '9:16', [{'kind': 'video'}]),
    ('easel-ppu-h3-local::minimax-h3-base', 6, '9:16', [{'kind': 'image'}, {'kind': 'image'}]),
    ('easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step', True, '9:16', [{'kind': 'image'}]),
])
def test_installed_h3_contract_rejects_unsupported_reference_specs(refs_module, model, seconds, ratio, refs):
    with pytest.raises(Exception):
        refs_module.validate_reference_spec(model, seconds, ratio, refs)


def test_ref11_accepts_actual_image_and_native_audio_reference(refs_module, media):
    frozen = refs_module.freeze_references(media[0], [
        {'kind': 'image', 'path': 'family/refs/mother.png'}, {'kind': 'video', 'path': 'family/refs/voice.mp4'}])
    refs_module.validate_reference_spec('easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step', 14, '9:16', frozen)
    refs_module.validate_reference_spec('easel-ppu-h3-local::minimax-h3-dense8', 12, '9:16', frozen[:1])
