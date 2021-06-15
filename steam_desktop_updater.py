#!/usr/bin/env python3

import sys
import os
from pathlib import *
import io
import glob
import zipfile
from configparser import ConfigParser
import logging
import typing as t

from PIL import Image
import vdf
from steam.utils import appcache


ICON_SIZES = {
    'max': 512,
}
DEFAULT_STEAM_CMD = 'xdg-open'


class DesktopFileParser(ConfigParser):
    def optionxform(self, option):
        return option


class SteamApp(object):
    def __init__(self, steam_root: Path, app_id: int, app_info: t.Mapping):
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

    def is_installed(self, library_folder: Path):
        installdir = self.app_info['config']['installdir']
        app_dir = library_folder / 'steamapps' / 'common' / installdir
        if app_dir.is_dir():
            for i, launch in self.app_info['config']['launch'].items():
                assert i.isdigit()
                try:
                    oslist = launch['config']['oslist']
                    is_windows = 'windows' in oslist
                except KeyError:
                    # Assume it's windows-only game
                    is_windows = True
                path_cls = PureWindowsPath if is_windows else PurePosixPath
                bin_path_abs = app_dir / path_cls(launch['executable'])
                if bin_path_abs.is_file():
                    return True
        return False

    def get_name(self):
        return self.app_info['common']['name']

    def get_desktop_entry(self, steam_cmd: str = DEFAULT_STEAM_CMD):
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

    def save_desktop_entry(self, destdir: Path, steam_cmd: str = DEFAULT_STEAM_CMD):
        app_desktop = DesktopFileParser()
        app_desktop.read_dict(self.get_desktop_entry())
        app_desktop_file = destdir / 'applications' / f'{self.desktop_name}.desktop'
        app_desktop_file.parent.mkdir(parents=True, exist_ok=True)
        with app_desktop_file.open('w') as df:
            app_desktop.write(df, space_around_delimiters=False)

    def get_icon_files(self):
        """
        Get name of the file containing icon(s)
        """
        icon_files = {}
        common_info = self.app_info['common']
        icons_dir = self.steam_root / 'steam' / 'games'
        for i, e in [('linuxclienticon', 'zip'), ('clienticon', 'ico')]:
            if i in common_info:
                icon_hash = common_info[i]
                logging.debug(f'{i} is set, searching it... ')
                icon_path = icons_dir / f'{icon_hash}.{e}'
                if icon_path.is_file():
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
    def __init__(self, icon_file: Path, datadir: Path, icon_name: str):
        self._file = icon_file
        self.datadir = datadir
        self.icon_name = icon_name

    def get_dest(self, size: int):
        destdir = self.datadir / 'icons' / 'hicolor' / f'{size}x{size}' / 'apps'
        if not destdir.is_dir():
            destdir.mkdir(parents=True)
        return destdir / f'{self.icon_name}.png'

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
        elif self._file.suffix == '.ico':
            logging.debug(f'Saving icon {self._file}')
            self.extract_ico()


def get_installed_apps(steam_root: Path):
    """
    Enumerate IDs of installed apps in given library
    """
    apps = []
    logging.info('Searching library folders')
    with (steam_root / 'steamapps' / 'libraryfolders.vdf').open('r') as lf:
        library_folders = vdf.load(lf)['LibraryFolders']
        for folder_path in [steam_root] + [Path(v) for k, v in library_folders.items() if k.isdigit()]:
            logging.info(f'Collecting apps in folder {folder_path}')
            for app in (folder_path / 'steamapps').glob('appmanifest_*.acf'):
                with app.open('r') as amf:
                    app_mainfest = vdf.load(amf)
                    app_state = {k.lower(): v for k, v in app_mainfest['AppState'].items()}
                    apps.append((folder_path, int(app_state['appid'])))
    return apps


def create_desktop_data(steam_root: Path, destdir: Path = None, steam_cmd: str = 'xdg-open'):
    logging.info('Loading appinfo.vdf')
    appinfo_data = {}
    with (steam_root / 'appcache' / 'appinfo.vdf').open('rb') as af:
        _, apps_gen = appcache.parse_appinfo(af)
        for app in apps_gen:
            appinfo_data[app['appid']] = app['data']['appinfo']

    if destdir is None:
        destdir = Path('~/.local/share').expanduser()

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
    parser.add_argument('steam_root', type=Path,
                        help='Path to Steam root directory, e.g. ~/.local/share/Steam')
    parser.add_argument('-d', '--datatir', type=Path, default=None, required=False,
                        help='Destination data dir where to create files (defaults to ~/.local/share)')
    parser.add_argument('-c', '--steam-command', default='xdg-open', required=False,
                        help='Steam command (defaults to xdg-open)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    create_desktop_data(steam_root=args.steam_root, destdir=args.datatir, steam_cmd=args.steam_command)
