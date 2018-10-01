#!/usr/bin/env python3

from steamfiles import appinfo
from steamfiles import acf
import sys
import os
import glob
import zipfile
from configparser import ConfigParser
from PIL import Image
import io


def get_installed_apps(library_folder):
    """
    Enumerate IDs of installed apps in given library
    """
    for app in glob.glob(os.path.join(library_folder, 'steamapps', 'appmanifest_*.acf')):
        with open(app, 'r') as amf:
            app_mainfest = acf.load(amf)
            # TODO maybe check if game is actually installed?
            yield app_mainfest['AppState']['appid']


def get_icon_source(steam_root, app_info):
    """
    Get name of the file containing icon(s)
    """
    common_info = app_info['sections'][b'appinfo'][b'common']
    icons_dir = os.path.join(steam_root, 'steam', 'games')
    for i in [b'linuxclienticon', b'clienticon', b'clienticns', b'clienttga', b'icon', b'logo', b'logo_small']:
        if i in common_info:
            icon_hash = common_info[i].decode()
            print(i, 'is set, searching it... ', end='', file=sys.stderr)
            for fmt in ['zip', 'ico']:
                icon_path = os.path.join(icons_dir, f'{icon_hash}.{fmt}')
                if os.path.isfile(icon_path):
                    print('found', os.path.relpath(icon_path, steam_root), file=sys.stderr)
                    return (icon_hash, icon_path)
            print('not found', file=sys.stderr)
    return (None, None)


def extract_icon_source(icon_source, destdir, icon_name):
    if zipfile.is_zipfile(icon_source):
        print(os.path.basename(icon_source), 'appears to be a zip file', file=sys.stderr)
        with zipfile.ZipFile(icon_source, 'r') as zf:
            for zi in zf.infolist():
                if not zi.is_dir() and zi.filename.endswith('.png'):
                    with zf.open(zi.filename) as img_file:
                        print('Saving icon', zi.filename, file=sys.stderr)
                        save_icon(img_file, destdir, icon_name)
    elif icon_source.endswith('.ico'):
        print('Saving icon', icon_source, file=sys.stderr)
        with open(icon_source, 'rb') as img_file:
            save_icon(img_file, destdir, icon_name)


def save_icon(img_file, destdir, icon_name):
    """
    Save given bytes-like object to given directory with given name
    """
    # FIXME we create here a new bytes-like objects because ZipExtFile is not seekable
    with io.BytesIO() as img_bytes:
        img_bytes.write(img_file.read())
        try:
            img = Image.open(img_bytes)
        except OSError as e:
            print(e, file=sys.stderr)
        else:
            h, w = img.size
            if h == w:
                sized_destdir = os.path.join(destdir, 'icons', 'hicolor', f'{h}x{w}', 'apps')
                os.makedirs(sized_destdir, exist_ok=True)
                dest = os.path.join(sized_destdir, f'{icon_name}.png')
                if img.format == 'PNG':
                    with open(dest, 'wb') as df:
                        img_bytes.seek(0)
                        df.write(img_bytes.read())
                else:
                    img.save(dest)
                img.close()
                return(dest)


def create_desktop_data(steam_root, destdir=None, steam_cmd='xdg-open'):
    with open(os.path.join(steam_root, 'appcache', 'appinfo.vdf'), 'rb') as af:
        appinfo_data = appinfo.load(af)
    with open(os.path.join(steam_root, 'steamapps', 'libraryfolders.vdf'), 'r') as lf:
        library_folders = []
        for k, v in acf.load(lf)['LibraryFolders'].items():
            if k.isdigit():
                library_folders.append(v)

    if destdir is None:
        destdir = os.path.join(os.environ.get('HOME'), '.local', 'share')

    for library_folder in library_folders:
        print('Processing library', library_folder, file=sys.stderr)
        for app_id in get_installed_apps(library_folder):
            app_info = appinfo_data[int(app_id)]
            app_name = app_info['sections'][b'appinfo'][b'common'][b'name'].decode()
            print('Processing app ID', app_id, ':', app_name, file=sys.stderr)

            app_icon_hash, app_icon_src = get_icon_source(steam_root, app_info)
            app_icon_name = f'steam_icon_{app_id}'
            if app_icon_src is not None:
                extract_icon_source(icon_source=app_icon_src, destdir=destdir, icon_name=app_icon_name)

            app_desktop_file = f'steam_app_{app_id}.desktop'
            app_desktop = ConfigParser()
            app_desktop.optionxform = str
            app_desktop['Desktop Entry'] = {
                'Type': 'Application',
                'Name': app_name,
                'Comment': 'Launch this game via Steam',
                'Exec': f'{steam_cmd} steam://rungameid/{app_id}',
                'Icon': app_icon_name,
                'Categories': 'Game;X-Steam;'
            }
            apps_destdir = os.path.join(destdir, 'applications')
            os.makedirs(apps_destdir, exist_ok=True)
            with open(os.path.join(apps_destdir, app_desktop_file), 'w') as df:
                app_desktop.write(df, space_around_delimiters=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Create desktop entries for Steam games.')
    parser.add_argument('steam_root', help='Path to Steam root directory, e.g. ~/.local/share/Steam')
    parser.add_argument('-d', '--datatir', default=None, required=False, help='Destination data dir where to create files (defaults to ~/.local/share)')
    parser.add_argument('-c', '--steam-command', default='xdg-open', required=False, help='Steam command (defaults to xdg-open)')
    args = parser.parse_args()
    create_desktop_data(steam_root=args.steam_root, destdir=args.datatir, steam_cmd=args.steam_command)
