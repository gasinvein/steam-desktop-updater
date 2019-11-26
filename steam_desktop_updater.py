#!/usr/bin/env python3

import sys
import os
import io
import glob
import zipfile
from configparser import ConfigParser
from PIL import Image
from steamfiles import appinfo, acf


ICON_SIZES = {
    'max': 512,
}
DEFAULT_STEAM_CMD = 'xdg-open'


class SteamApp(object):
    def __init__(self, steam_root, app_id, app_info=None):
        self.app_id = app_id
        if app_info is None:
            with open(os.path.join(steam_root, 'appcache', 'appinfo.vdf'), 'rb') as af:
                steam_appinfo = appinfo.load(af)
                self.app_info = steam_appinfo[int(app_id)]
        else:
            self.app_info = app_info
        self.steam_root = steam_root
        self.desktop_name = f'steam_app_{app_id}'
        self.icon_name = f'steam_icon_{app_id}'

    def get_name(self):
        return self.app_info['sections'][b'appinfo'][b'common'][b'name'].decode()

    def get_desktop_entry(self, steam_cmd=DEFAULT_STEAM_CMD):
        app_name = self.get_name()
        return {
            'Desktop Entry': {
                'Type': 'Application',
                'Name': app_name,
                'Comment': 'Launch this game via Steam',
                'Exec': f'{steam_cmd} steam://rungameid/{self.app_id}',
                'Icon': self.icon_name,
                'Categories': 'Game;X-Steam;'
            }
        }

    def save_desktop_entry(self, destdir, steam_cmd=DEFAULT_STEAM_CMD):
        app_desktop = ConfigParser()
        app_desktop.optionxform = str
        app_desktop.read_dict(self.get_desktop_entry())
        apps_destdir = os.path.join(destdir, 'applications')
        app_desktop_file = f'{self.desktop_name}.desktop'
        os.makedirs(apps_destdir, exist_ok=True)
        with open(os.path.join(apps_destdir, app_desktop_file), 'w') as df:
            app_desktop.write(df, space_around_delimiters=False)

    def get_icon_store(self):
        """
        Get name of the file containing icon(s)
        """
        common_info = self.app_info['sections'][b'appinfo'][b'common']
        icons_dir = os.path.join(self.steam_root, 'steam', 'games')
        for i in [b'linuxclienticon', b'clienticon', b'clienticns', b'clienttga', b'icon', b'logo', b'logo_small']:
            if i in common_info:
                icon_hash = common_info[i].decode()
                print(i, 'is set, searching it... ', end='', file=sys.stderr)
                for fmt in ['zip', 'ico']:
                    icon_path = os.path.join(icons_dir, f'{icon_hash}.{fmt}')
                    if os.path.isfile(icon_path):
                        print('found', os.path.relpath(icon_path, self.steam_root), file=sys.stderr)
                        return SteamIconStore(icon_path)

    def extract_icons(self, destdir):
        icon_store = self.get_icon_store()
        if icon_store is not None:
            icon_store.extract_icons(destdir=destdir, icon_name=self.icon_name)


class SteamIconStore(object):
    def __init__(self, icon_file):
        self._file = icon_file

    def extract_icons(self, destdir, icon_name):
        if zipfile.is_zipfile(self._file):
            print(os.path.basename(self._file), 'appears to be a zip file', file=sys.stderr)
            with zipfile.ZipFile(self._file, 'r') as zf:
                for zi in zf.infolist():
                    if not zi.is_dir() and zi.filename.endswith('.png'):
                        print('Saving icon', zi.filename, file=sys.stderr)
                        # FIXME we create here a new bytes-like objects because ZipExtFile is not seekable
                        with io.BytesIO() as img_bytes:
                            with zf.open(zi.filename) as img_file:
                                img_bytes.write(img_file.read())
                            save_icon(img_bytes, destdir, icon_name)
        elif self._file.endswith('.ico'):
            print('Saving icon', self._file, file=sys.stderr)
            with open(self._file, 'rb') as img_file:
                save_icon(img_file, destdir, icon_name)


def save_icon(img_file, destdir, icon_name):
    """
    Save given bytes-like object to given directory with given name
    """
    try:
        img = Image.open(img_file)
    except OSError as e:
        print(e, file=sys.stderr)
    else:
        h, w = img.size
        save_direct = True
        if h == w:
            s = h
            # Resize icon if it's too large
            m = ICON_SIZES['max']
            max_size_dest = os.path.join(destdir, 'icons', 'hicolor', f'{m}x{m}', 'apps', f'{icon_name}.png')
            if s > m:
                if os.path.isfile(max_size_dest):
                    return
                print('Icon size', f'{s}x{s}', 'is too large, resizing')
                new_img = img.resize((m, m), resample=Image.LANCZOS)
                img.close()
                img = new_img
                s = m
                save_direct = False
            if img.format != 'PNG':
                save_direct = False
            dest = os.path.join(destdir, 'icons', 'hicolor', f'{s}x{s}', 'apps', f'{icon_name}.png')
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if save_direct:
                with open(dest, 'wb') as df:
                    img_file.seek(0)
                    df.write(img_file.read())
            else:
                img.save(dest)
            img.close()
            return(dest)

def get_installed_apps(library_folder):
    """
    Enumerate IDs of installed apps in given library
    """
    for app in glob.glob(os.path.join(library_folder, 'steamapps', 'appmanifest_*.acf')):
        with open(app, 'r') as amf:
            app_mainfest = acf.load(amf)
            # TODO maybe check if game is actually installed?
            yield app_mainfest['AppState']['appid']


def create_desktop_data(steam_root, destdir=None, steam_cmd='xdg-open'):
    print('Loading appinfo.vdf', file=sys.stderr)
    with open(os.path.join(steam_root, 'appcache', 'appinfo.vdf'), 'rb') as af:
        appinfo_data = appinfo.load(af)

    print('Searching library folders', file=sys.stderr)
    library_folders = []
    with open(os.path.join(steam_root, 'steamapps', 'libraryfolders.vdf'), 'r') as lf:
        for k, v in acf.load(lf)['LibraryFolders'].items():
            if k.isdigit():
                library_folders.append(v)

    if destdir is None:
        destdir = os.path.join(os.environ.get('HOME'), '.local', 'share')

    for library_folder in library_folders:
        print('Processing library', library_folder, file=sys.stderr)
        for app_id in get_installed_apps(library_folder):
            app = SteamApp(steam_root=steam_root, app_id=app_id, app_info=appinfo_data[int(app_id)])
            print('Processing app ID', app_id, ':', app.get_name(), file=sys.stderr)
            app.save_desktop_entry(destdir)
            app.extract_icons(destdir)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Create desktop entries for Steam games.')
    parser.add_argument('steam_root', help='Path to Steam root directory, e.g. ~/.local/share/Steam')
    parser.add_argument('-d', '--datatir', default=None, required=False, help='Destination data dir where to create files (defaults to ~/.local/share)')
    parser.add_argument('-c', '--steam-command', default='xdg-open', required=False, help='Steam command (defaults to xdg-open)')
    args = parser.parse_args()
    create_desktop_data(steam_root=args.steam_root, destdir=args.datatir, steam_cmd=args.steam_command)
