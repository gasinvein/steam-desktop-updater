#!/usr/bin/env python3

from steamfiles import appinfo
from steamfiles import acf
import sys
import os
import glob
import zipfile
import shutil
from configparser import ConfigParser
from PIL import Image
from tempfile import gettempdir


def get_installed_apps(library_folder):
    for app in glob.glob(os.path.join(library_folder, 'steamapps', 'appmanifest_*.acf')):
        with open(app, 'r') as amf:
            app_mainfest = acf.load(amf)
            # TODO maybe check if game is actually installed?
            yield app_mainfest['AppState']['appid']


def extract_icons(steam_root, icon_hash, icon_path):
    icons = {}
    tmpdir = os.path.join(gettempdir(), 'steam-icons')
    os.makedirs(tmpdir, exist_ok=True)
    if icon_path.endswith('.zip'):
        with zipfile.ZipFile(icon_path, 'r') as zf:
            for zi in zf.infolist():
                if not zi.is_dir() and zi.filename.endswith('.png'):
                    with zf.open(zi.filename) as img_file:
                        try:
                            img = Image.open(img_file)
                            h, w = img.size
                            if h == w:
                                dest = os.path.join(tmpdir, icon_hash)
                                print('Extracting', zi.filename, 'to', dest)
                                zf.extract(zi.filename, dest)
                                icons[h] = os.path.join(dest, zi.filename)
                            img.close()
                        except OSError as e:
                            print(zi.filename, ":", e, file=sys.stderr)
    elif icon_path.endswith('.ico'):
        try:
            with Image.open(icon_path) as img:
                h, w = img.size
                if h == w:
                    dest = os.path.join(tmpdir, icon_hash + '.png')
                    img.save(dest, 'png')
                    icons[h] = dest
        except OSError as e:
            print(icon_path, ":", e, file=sys.stderr)
    else:
        raise ValueError('Don\'t know how to handle', icon_path)
    return icons


def get_icons(steam_root, app_info):
    """
    Returns icon store path in either format
    """
    common_info = app_info['sections'][b'appinfo'][b'common']
    icons_dir = os.path.join(steam_root, 'steam', 'games')
    for i in [b'linuxclienticon', b'clienticon', b'clienticns', b'clienttga', b'icon', b'logo', b'logo_small']:
        if i in common_info:
            icon_hash = common_info[i].decode()
            print(i, 'is set, searching it...')
            for fmt in ['zip', 'ico']:
                icon_path = os.path.join(icons_dir, icon_hash + '.' + fmt)
                if os.path.isfile(icon_path):
                    return extract_icons(steam_root, icon_hash, icon_path)
    return None


def load_installed_apps(steam_root, prefix=None, steam_cmd='steam'):
    with open(os.path.join(steam_root, 'appcache', 'appinfo.vdf'), 'rb') as af:
        appinfo_data = appinfo.load(af)
    with open(os.path.join(steam_root, 'steamapps', 'libraryfolders.vdf'), 'r') as lf:
        library_folders = []
        for k, v in acf.load(lf)['LibraryFolders'].items():
            if k.isdigit():
                library_folders.append(v)
        print(library_folders)

    if prefix is None:
        prefix = os.path.join(os.environ.get('HOME'), '.local')

    for library_folder in library_folders:
        for app_id in get_installed_apps(library_folder):
            app_info = appinfo_data[int(app_id)]
            print(app_info['sections'][b'appinfo'][b'common'][b'name'])

            app_icons = get_icons(steam_root, app_info)
            app_icon_name = f'steam_icon_{app_id}'
            if app_icons is not None:
                for size, icon_src in app_icons.items():
                    icon_dest_dir = os.path.join(prefix, 'share', 'icons', 'hicolor', f'{size}x{size}', 'apps')
                    icon_dest = os.path.join(icon_dest_dir, f'{app_icon_name}.png')
                    os.makedirs(icon_dest_dir, exist_ok=True)
                    shutil.copyfile(icon_src, icon_dest)

            app_desktop_file = f'steam_app_{app_id}_link.desktop'
            app_desktop = ConfigParser()
            app_desktop.optionxform = str
            app_desktop['Desktop Entry'] = {
                'Type': 'Application',
                'Name': app_info['sections'][b'appinfo'][b'common'][b'name'].decode(),
                'Comment': 'Launch this game via Steam',
                'Exec': f'{steam_cmd} steam://rungameid/{app_id}',
                'Icon': app_icon_name,
                'Categories': 'Game;X-Steam;'
            }
            with open(os.path.join(prefix, 'share', 'applications', app_desktop_file), 'w') as df:
                app_desktop.write(df, space_around_delimiters=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Create desktop entries for Steam games.')
    parser.add_argument('steam_root', help='path to Steam installation')
    parser.add_argument('prefix', default=None, nargs='?', help='prefix where to create files')
    parser.add_argument('-c', '--steam-command', default='xdg-open', required=False, help='Steam command (defaults to xdg-open)')
    args = parser.parse_args()
    load_installed_apps(steam_root=args.steam_root, prefix=args.prefix, steam_cmd=args.steam_command)
