"""Build a Windows portable distribution using the local CPython installation."""
import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'dist' / 'MediaViewer')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Build on Windows for Windows.')
    binaries = [shutil.which(name) for name in ('ffmpeg', 'ffprobe')]
    if not all(binaries):
        parser.error('The build computer needs ffmpeg and ffprobe in PATH.')
    output = args.output.resolve()
    if output.exists():
        parser.error('Output already exists; choose a new --output directory.')
    runtime = output / 'python'
    base = pathlib.Path(sys.base_prefix)
    runtime.mkdir(parents=True)
    for name in ('Lib', 'DLLs'):
        shutil.copytree(base / name, runtime / name,
                        ignore=shutil.ignore_patterns('site-packages', '__pycache__'))
    for pattern in ('*.exe', '*.dll', 'LICENSE*'):
        for source in base.glob(pattern):
            shutil.copy2(source, runtime)
    python = str(runtime / 'python.exe')
    subprocess.run([python, '-I', '-m', 'ensurepip'], check=True)
    subprocess.run([python, '-I', '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
    for name in ('mediaviewer', 'static'):
        shutil.copytree(ROOT / name, output / name,
                        ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('requirements.txt', 'requirements-ai.txt', 'LICENSE', 'README.md'):
        shutil.copy2(ROOT / name, output)
    # Keep the FFmpeg distribution intact, including its license and shared DLLs.
    ffroot = pathlib.Path(binaries[0]).parent
    if ffroot.name.lower() == 'bin':
        ffroot = ffroot.parent
    shutil.copytree(ffroot, output / 'ffmpeg')
    ffbin = (output / 'ffmpeg' / pathlib.Path(binaries[0]).relative_to(ffroot)).parent
    if not (ffbin / 'ffprobe.exe').is_file():
        raise RuntimeError('ffprobe must belong to the same FFmpeg distribution')
    relative_bin = ffbin.relative_to(output)
    (output / 'MediaViewer.bat').write_text(
        '@echo off\nsetlocal\ncd /d "%~dp0"\n'
        'set PYTHONHOME=\nset PYTHONPATH=\nset PYTHONNOUSERSITE=1\n'
        'set PYTHONUTF8=1\nset PYTHONIOENCODING=utf-8\n'
        f'set "PATH=%~dp0{relative_bin};%PATH%"\n'
        '"%~dp0python\\python.exe" -m mediaviewer %*\nif errorlevel 1 pause\n',
        encoding='ascii')
    subprocess.run([python, '-s', '-c', 'import mediaviewer.app'], cwd=output, check=True)
    shutil.make_archive(str(output), 'zip', output.parent, output.name)
    print(f'Ready: {output}.zip')


if __name__ == '__main__':
    main()

