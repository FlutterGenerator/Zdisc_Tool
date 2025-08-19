# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: test.py
# Bytecode version: 3.10.0rc2 (3439)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

import os
import re
import shutil
import zstandard as zstd
import requests
from pathlib import Path
from rich.console import Console
import zipfile
import io
console = Console()
DICT_MAGIC = b'7\xa40\xec'
DICT_FOLDER = 'extracted_dicts'
INPUT_FILE = 'mini_obbzsdic_obb.pak'

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def find_all_occurrences(data, pattern: bytes):
    return [m.start() for m in re.finditer(re.escape(pattern), data)]

def decompress_data(data, zdict):
    data = bytes([y ^ 121 for y in data])
    c = zstd.ZstdCompressionDict(zdict)
    d = zstd.ZstdDecompressor(dict_data=c)
    return d.decompress(data)

def binary_chop_optimize(data, zdict, target_size, min_ratio=0.7):

    def try_compress(chunk):
        cctx = zstd.ZstdCompressor(level=3, dict_data=zstd.ZstdCompressionDict(zdict), write_checksum=False, write_content_size=False)
        return cctx.compress(chunk)
    left, right = (0, len(data))
    best_chunk, best_compressed = (None, None)
    while left <= right:
        mid = (left + right) // 2
        chunk = data[:mid]
        try:
            compressed = try_compress(chunk)
            compressed_size = len(compressed)
            if compressed_size <= target_size:
                if compressed_size / target_size > min_ratio:
                    return (compressed, mid / len(data))
                left = mid + 1
                best_chunk, best_compressed = (chunk, compressed)
            else:
                right = mid - 1
        except Exception:
            right = mid - 1
    if best_compressed and best_chunk and (len(best_chunk) > 0):
        return (best_compressed, len(best_chunk) / len(data))
    return None

def compress_with_size_control(data, zdict, target_size, filename=''):
    strategies = [('Ultra compression', 22, 1.0), ('High compression', 12, 1.0), ('Balanced compression', 3, 1.0), ('Fast compression', 1, 1.0), ('Light truncation', 3, 0.95), ('Moderate truncation', 3, 0.85), ('Aggressive truncation', 3, 0.7), ('Binary chop optimization', None, None)]
    best_result, best_ratio = (None, 0)
    for desc, level, ratio in strategies:
        if level is None:
            optimized = binary_chop_optimize(data, zdict, target_size)
            if optimized:
                compressed, eff_ratio = optimized
                compressed = bytes([y ^ 121 for y in compressed])
                if len(compressed) <= target_size and eff_ratio > best_ratio:
                    best_ratio, best_result = (eff_ratio, compressed + bytes(target_size - len(compressed)))
                    console.print(f'✓ Binary chop succeeded ({eff_ratio:.1%} data retained)')
        else:
            try:
                current_data = data[:int(len(data) * ratio)] if ratio < 1.0 else data
                cctx = zstd.ZstdCompressor(level=level, dict_data=zstd.ZstdCompressionDict(zdict), write_checksum=False, write_content_size=False)
                compressed = bytes([y ^ 121 for y in cctx.compress(current_data)])
                if len(compressed) <= target_size:
                    actual_ratio = len(compressed) / target_size
                    if actual_ratio > best_ratio:
                        best_ratio, best_result = (actual_ratio, compressed + bytes(target_size - len(compressed)))
                        console.print(f'✓ Reimported {filename}')
                        if actual_ratio >= 0.98:
                            return best_result
            except Exception:
                continue
    if best_result:
        if best_ratio < 0.8:
            console.print(f'⚠ Low efficiency ({best_ratio:.1%}) for {filename}')
        return best_result
    console.print(f'✖ Critical: Could not compress {filename} to required size')
    return

def load_external_dict(dict_path='dict_000.zdict'):
    dict_file = Path(dict_path)
    if not dict_file.exists():
        console.print(f"❌ Dictionary file '{dict_path}' not found!", style='bold red')
        return
    return dict_file.read_bytes()

def unpack_pak(pak_file: str):
    unpack_dir = Path('UNPACK')
    unpack_dir.mkdir(exist_ok=True)
    console.print(f'\n🚀 Starting extraction of {pak_file}...', style='bold cyan')
    zdict = load_external_dict()
    if zdict is None:
        return
    pak_data = Path(pak_file).read_bytes()
    chunk_starts = find_all_occurrences(pak_data, b'Q\xccV\x84') + [len(pak_data)]
    for i in range(len(chunk_starts) - 1):
        try:
            chunk_data = pak_data[chunk_starts[i]:chunk_starts[i + 1]]
            decompressed = decompress_data(chunk_data, zdict)
            (unpack_dir / f'file_{i:06d}.uasset').write_bytes(decompressed)
        except Exception as e:
            console.print(f'\n❌ Failed chunk {i}: {e}', style='red')
    console.print(f'\n✅ Extraction complete → {unpack_dir}/', style='bold green')

def repack_pak(pak_file: str):
    edited_dir = Path('Edited_uasset')
    repacked_dir = Path('repacked')
    repacked_dir.mkdir(exist_ok=True)
    if not edited_dir.exists():
        console.print(f'❌ Error: {edited_dir} directory not found!', style='bold red')
        return
    console.print(f'\nStarting repack of {pak_file}...', style='bold cyan')
    zdict = load_external_dict()
    if zdict is None:
        console.print('❌ Repacking aborted due to missing dictionary.', style='bold red')
        return
    with open(pak_file, 'rb') as f:
        original_data = f.read()
    original_size = len(original_data)
    chunk_markers = find_all_occurrences(original_data, b'Q\xccV\x84')
    new_pak_data = bytearray(original_data[:chunk_markers[0]] if chunk_markers else [])
    total_files = len(chunk_markers)
    modified_count = 0
    skipped_files = []
    for i, marker_pos in enumerate(chunk_markers):
        edited_file = edited_dir / f'file_{i:06d}.uasset'
        original_chunk_end = chunk_markers[i + 1] if i + 1 < len(chunk_markers) else len(original_data)
        original_chunk_size = original_chunk_end - marker_pos
        if (i + 1) % 1000 == 0 or i + 1 == total_files:
            console.print(f'Repacking progress: {i + 1}/{total_files}', end='\r')
        if edited_file.exists():
            file_data = edited_file.read_bytes()
            compressed = compress_with_size_control(file_data, zdict, original_chunk_size, edited_file.name)
            if compressed:
                new_pak_data.extend(compressed)
                modified_count += 1
            else:
                new_pak_data.extend(original_data[marker_pos:original_chunk_end])
                skipped_files.append(edited_file.name)
        else:
            new_pak_data.extend(original_data[marker_pos:original_chunk_end])
    if len(new_pak_data) < original_size:
        new_pak_data += bytes(original_size - len(new_pak_data))
    else:
        new_pak_data = new_pak_data[:original_size]
    output_file = repacked_dir / f'{pak_file}'
    output_file.write_bytes(new_pak_data)
    console.print('\n==================================================')
    console.print('✅ Repacking complete! ', style='bold green')
    if skipped_files:
        console.print('\n⚠ Files that required original data:', style='yellow')
        for f in skipped_files[:5]:
            console.print(f'- {f}')
        if len(skipped_files) > 5:
            console.print(f'- ...and {len(skipped_files) - 5} more')
    console.print(f'💾 Output: {output_file}')
    console.print('==================================================')

def extract_dictionaries():
    os.makedirs(DICT_FOLDER, exist_ok=True)
    if not Path(INPUT_FILE).exists():
        console.print(f"❌ Input file '{INPUT_FILE}' not found!", style='bold red')
        return
    data = Path(INPUT_FILE).read_bytes()
    offset, count = (0, 0)
    dict_size = 1048576
    while True:
        idx = data.find(DICT_MAGIC, offset)
        if idx == -1:
            break
        chunk = data[idx:min(idx + dict_size, len(data))]
        try:
            zstd.ZstdCompressionDict(chunk)
            path = f'{DICT_FOLDER}/dict_{count:03d}.zdict'
            Path(path).write_bytes(chunk)
            console.print(f'✅ Dictionary #{count} extracted → {path}')
            count += 1
        except zstd.ZstdError:
            pass
        offset = idx + len(DICT_MAGIC)
    console.print(f'🎉 Total dictionaries found: {count}')

def clear_unpack_folder():
    unpack_dir = Path('UNPACK')
    if unpack_dir.exists():
        shutil.rmtree(unpack_dir)
        console.print("✅ 'UNPACK' folder deleted!", style='bold green')
    else:
        console.print("⚠ 'UNPACK' folder does not exist!", style='yellow')

def download_x_effect_fixed():
    console.print('\n✨ [bold cyan]X Effect Making[/bold cyan]')
    url = 'https://github.com/Rpad1337/zdisc-keys/raw/refs/heads/main/file_027139.uasset'
    try:
        response = requests.get(url)
        response.raise_for_status()
    except Exception as e:
        console.print(f'❌ Download failed: {e}', style='bold red')
        return None
    save_dir = Path('Edited_uasset')
    save_dir.mkdir(exist_ok=True)
    filename = url.split('/')[-1]
    save_path = save_dir / filename
    try:
        save_path.write_bytes(response.content)
        console.print(f'✅ File saved to [green]{save_path}[/green]')
    except Exception as e:
        console.print(f'❌ Saving failed: {e}', style='bold red')

def download_autoheadshot_fixed():
    console.print('\n✨ [bold cyan]Auto Headshot Making[/bold cyan]')
    url = 'https://github.com/Rpad1337/zdisc-keys/raw/refs/heads/main/file_027157.uasset'
    try:
        response = requests.get(url)
        response.raise_for_status()
    except Exception as e:
        console.print(f'❌ Download failed: {e}', style='bold red')
        return None
    save_dir = Path('Edited_uasset')
    save_dir.mkdir(exist_ok=True)
    filename = url.split('/')[-1]
    save_path = save_dir / filename
    try:
        save_path.write_bytes(response.content)
        console.print(f'✅ File saved to [green]{save_path}[/green]')
    except Exception as e:
        console.print(f'❌ Saving failed: {e}', style='bold red')

def download_and_extract_edited_uasset_zip():
    console.print('\n✨ [bold cyan]White Body Making[/bold cyan]')
    url = 'https://github.com/Rpad1337/zdisc-keys/raw/refs/heads/main/Edited_uasset1.zip'
    save_dir = Path('Edited_uasset')
    try:
        response = requests.get(url)
        response.raise_for_status()
    except Exception as e:
        console.print(f'❌ Download failed: {e}', style='bold red')
        return False
    save_dir.mkdir(exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_ref:
            zip_ref.extractall(save_dir)
        console.print(f'✅ Files saved to [green]{save_dir}[/green]')
        return True
    except Exception as e:
        console.print(f'❌ Extraction failed: {e}', style='bold red')
        return False

def show_auto_config_menu():
    clear_screen()
    console.print('\n✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨', style='bold magenta')
    console.print('[bold bright_white on magenta]   ⚙ AUTO CONFIG FEATURES MENU ⚙   [/]', justify='center')
    console.print('✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨✨\n', style='bold magenta')
    features = [('🎯', 'X Effect'), ('🔫', 'Auto Headshot'), ('⚪', 'White Body'), ('🔙', 'Return to Main Menu')]
    console.print('\n[bold bright_white]Select a feature to configure:[/bold bright_white]\n')
    for i, (emoji, text) in enumerate(features, 1):
        console.print(f'[cyan]{i}.[/cyan] {emoji}  [white]{text}[/white]')
    choice = console.input('\n[bold green]➡ Your choice (1-4): [/bold green]').strip()
    if choice == '1':
        download_x_effect_fixed()
        input('\nPress Enter to return...')
    elif choice == '2':
        download_autoheadshot_fixed()
        input('\nPress Enter to return...')
    elif choice == '3':
        download_and_extract_edited_uasset_zip()
        input('\nPress Enter to return...')
    else:
        if choice == '4':
            return
        console.print('❌ [red]Invalid choice![/red]')
        input('\nPress Enter to continue...')
    show_auto_config_menu()

def show_menu():
    clear_screen()
    console.print('==================================================', style='cyan')
    console.print('[bold magenta]' + 'Tool Menu'.center(50) + '[/bold magenta]')
    console.print('[green]' + 'Made by @KyrenixSoft'.center(50) + ' 💻[/green]')
    console.print('==================================================', style='cyan')
    console.print('[yellow]1.[/yellow] 📦 Unpack')
    console.print('[yellow]2.[/yellow] 🛠️ Repack')
    console.print('[yellow]3.[/yellow] 📚 Extract Dictionaries')
    console.print('[yellow]4.[/yellow] 🧹 Clear UNPACK Folder')
    console.print('[yellow]5.[/yellow] 🤖 Auto Config Features')
    console.print('[yellow]6.[/yellow] ❌ Exit')
    return console.input('[bold cyan]Select option (1-6): [/bold cyan]').strip()

def select_pak_file():
    pak_files = [f for f in os.listdir() if f.lower().endswith('.pak')]
    if not pak_files:
        console.print('❌ No .pak files found!', style='bold red')
        return
    for i, f in enumerate(pak_files, 1):
        console.print(f'{i}. {f}')
    try:
        return pak_files[int(console.input('Select a .pak file by number: ')) - 1]
    except (ValueError, IndexError):
        console.print('❌ Invalid selection!', style='bold red')
        return None

def main():
    while True:
        choice = show_menu()
        if choice == '1':
            pak = select_pak_file()
            if pak:
                unpack_pak(pak)
                input('\nPress Enter to continue...')
        elif choice == '2':
            pak = select_pak_file()
            if pak:
                repack_pak(pak)
                input('\nPress Enter to continue...')
        elif choice == '3':
            extract_dictionaries()
            input('\nPress Enter to continue...')
        elif choice == '4':
            clear_unpack_folder()
            input('\nPress Enter to continue...')
        elif choice == '5':
            show_auto_config_menu()
        else:
            if choice == '6':
                console.print('👋 Exiting... Bye!')
                return
            console.print('❌ Invalid option!')
            input('\nPress Enter to try again...')
if __name__ == '__main__':
    main()