#!/usr/bin/env python3

import sys
import os
import io
import glob
import zipfile
from configparser import ConfigParser
import logging
from PIL import Image
import vdf
from steam.utils import appcache


ICON_SIZES = {
    'max': 512,
}
DEFAULT_STEAM_CMD = 'xdg-open'


class SteamApp(object):
    def __init__(self, steam_root, app_id, app_info):
        self.app_id = app_id
        self.app_info = app_info
        self.steam_root = steam_root
        self.desktop_name = f'steam_app_{app_id}'
        self.icon_name = f'steam_icon_{app_id}'

    def is_game(self):
        if 'common' in self.app_info:
            if self.app_info['common']['type'].lower() == 'game':
                return True
        return False

    def is_installed(self, library_folder):
        app_dir = os.path.join(library_folder, 'steamapps', 'common',
                               self.app_info['config']['installdir'])
        if os.path.isdir(app_dir):
            for i, launch in self.app_info['config']['launch'].items():
                assert i.isdigit()
                bin_path = launch['executable']
                try:
                    oslist = launch['config']['oslist']
                except KeyError:
                    # Assume it's windows-only game
                    oslist = ['windows']
                if 'windows' in oslist:
                    bin_path = bin_path.replace('\\', '/')
                bin_path_abs = os.path.join(app_dir, bin_path)
                if os.path.isfile(bin_path_abs):
                    return True
        return False

    def get_name(self):
        return self.app_info['common']['name']

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

    def get_icon_files(self):
        """
        Get name of the file containing icon(s)
        """
        icon_files = {}
        common_info = self.app_info['common']
        icons_dir = os.path.join(self.steam_root, 'steam', 'games')
        for i, e in [('linuxclienticon', 'zip'), ('clienticon', 'ico')]:
            if i in common_info:
                icon_hash = common_info[i]
                logging.debug(f'{i} is set, searching it... ')
                icon_path = os.path.join(icons_dir, f'{icon_hash}.{e}')
                if os.path.isfile(icon_path):
                    logging.debug(f'found {icon_path}')
                    icon_files[i] = icon_path
        return icon_files

    def extract_icons(self, destdir):
        icon_files = self.get_icon_files()
        if not icon_files:
            logging.warning(f'No icons found')
            return
        for pref in ['linuxclienticon', 'clienticon']:
            if pref in icon_files:
                extractor = SteamIconExtractor(icon_files[pref], destdir, self.icon_name)
                extractor.extract()
                return


class SteamIconExtractor(object):
    def __init__(self, icon_file, datadir, icon_name):
        self._file = icon_file
        self.datadir = datadir
        self.icon_name = icon_name

    def get_dest(self, size):
        destdir = os.path.join(self.datadir, 'icons', 'hicolor', f'{size}x{size}', 'apps')
        if not os.path.isdir(destdir):
            os.makedirs(destdir)
        return os.path.join(destdir, f'{self.icon_name}.png')

    def extract_zip_png(self):
        with zipfile.ZipFile(self._file, 'r') as zf:
            for zi in zf.infolist():
                if zi.is_dir() or not zi.filename.lower().endswith('.png'):
                    continue
                logging.debug(f'Saving icon {zi.filename}')
                with zf.open(zi.filename) as img_file:
                    try:
                        img = Image.open(img_file)
                    except OSError as e:
                        logging.info(e)
                        continue
                    h, w = img.size
                    assert h == w
                    assert img.format == 'PNG'
                    img_file.seek(0)
                    with open(self.get_dest(h), 'wb') as d:
                        d.write(img_file.read())
                    img.close()

    def extract_ico(self):
        with Image.open(self._file) as img:
            assert img.format == 'ICO'
            for size in img.ico.sizes():
                subimg = img.ico.getimage(size)
                h, w = subimg.size
                assert h == w
                if subimg.size != size:
                    logging.warning(f'Expected size {size[0]}x{size[1]}, got {h}x{w}')
                logging.debug(f'Saving icon size {h}x{w}')
                subimg.save(self.get_dest(h), format='PNG')

    def extract(self):
        logging.info(f'Extracting icon(s) from {self._file}')
        if zipfile.is_zipfile(self._file):
            logging.debug(f'{self._file} appears to be a zip file')
            self.extract_zip_png()
        elif self._file.endswith('.ico'):
            logging.debug(f'Saving icon {self._file}')
            self.extract_ico()


def get_installed_apps(steam_root):
    """
    Enumerate IDs of installed apps in given library
    """
    apps = []
    logging.info('Searching library folders')
    with open(os.path.join(steam_root, 'steamapps', 'libraryfolders.vdf'), 'r') as lf:
        library_folders = vdf.load(lf)['LibraryFolders']
        for folder_path in [steam_root] + [v for k, v in library_folders.items() if k.isdigit()]:
            logging.info(f'Collecting apps in folder {folder_path}')
            for app in glob.glob(os.path.join(folder_path, 'steamapps', 'appmanifest_*.acf')):
                with open(app, 'r') as amf:
                    app_mainfest = vdf.load(amf)
                    app_state = {k.lower(): v for k, v in app_mainfest['AppState'].items()}
                    apps.append((folder_path, int(app_state['appid'])))
    return apps


def create_desktop_data(steam_root, destdir=None, steam_cmd='xdg-open'):
    logging.info('Loading appinfo.vdf')
    appinfo_data = {}
    with open(os.path.join(steam_root, 'appcache', 'appinfo.vdf'), 'rb') as af:
        _, apps_gen = appcache.parse_appinfo(af)
        for app in apps_gen:
            appinfo_data[app['appid']] = app['data']['appinfo']

    if destdir is None:
        destdir = os.path.join(os.environ.get('HOME'), '.local', 'share')

    for library_folder, app_id in get_installed_apps(steam_root):
        app = SteamApp(steam_root=steam_root, app_id=app_id, app_info=appinfo_data[app_id])
        if not app.is_game():
            continue
        if not app.is_installed(library_folder):
            continue
        logging.info(f'Processing app ID {app_id} : {app.get_name()}')
        app.save_desktop_entry(destdir)
        app.extract_icons(destdir)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Create desktop entries for Steam games.')
    parser.add_argument('steam_root', help='Path to Steam root directory, e.g. ~/.local/share/Steam')
    parser.add_argument('-d', '--datatir', default=None, required=False, help='Destination data dir where to create files (defaults to ~/.local/share)')
    parser.add_argument('-c', '--steam-command', default='xdg-open', required=False, help='Steam command (defaults to xdg-open)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    create_desktop_data(steam_root=args.steam_root, destdir=args.datatir, steam_cmd=args.steam_command)
