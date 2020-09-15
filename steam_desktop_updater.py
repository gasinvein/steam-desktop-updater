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
        #FIXME dumb way to skip non-game apps
        return 'common' in self.app_info

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
        icon_files = []
        common_info = self.app_info['common']
        icons_dir = os.path.join(self.steam_root, 'steam', 'games')
        for i in ['linuxclienticon', 'clienticon', 'clienticns', 'clienttga', 'icon', 'logo', 'logo_small']:
            if i in common_info:
                icon_hash = common_info[i]
                logging.debug(f'{i} is set, searching it... ')
                for fmt in ['zip', 'ico']:
                    icon_path = os.path.join(icons_dir, f'{icon_hash}.{fmt}')
                    if os.path.isfile(icon_path):
                        logging.debug(f'found {icon_path}')
                        icon_files.append(icon_path)
        return icon_files

    def extract_icons(self, destdir):
        icon_files = self.get_icon_files()
        if not icon_files:
            logging.warning(f'No icons found')
            return
        for icon_file in icon_files:
            extractor = SteamIconExtractor(icon_file, destdir, self.icon_name)
            extractor.extract_icons()


class SteamIconExtractor(object):
    def __init__(self, icon_file, datadir, icon_name):
        self._file = icon_file
        self.datadir = datadir
        self.icon_name = icon_name

    def extract_icons(self):
        logging.info(f'Extracting icon(s) from {self._file}')
        if zipfile.is_zipfile(self._file):
            logging.debug(f'{self._file} appears to be a zip file')
            with zipfile.ZipFile(self._file, 'r') as zf:
                for zi in zf.infolist():
                    if not zi.is_dir() and zi.filename.endswith('.png'):
                        logging.debug(f'Saving icon {zi.filename}')
                        with zf.open(zi.filename) as img_file:
                            self.save_icon(img_file)
        elif self._file.endswith('.ico'):
            logging.debug(f'Saving icon {self._file}')
            with open(self._file, 'rb') as img_file:
                self.save_icon(img_file)

    def save_icon(self, img_file):
        """
        Save given bytes-like object to given directory with given name
        """
        try:
            img = Image.open(img_file)
        except OSError as e:
            logging.info(e)
            return
        h, w = img.size
        save_direct = True
        if h != w:
            return
        s = h
        # Resize icon if it's too large
        m = ICON_SIZES['max']
        max_size_dest = os.path.join(self.datadir, 'icons', 'hicolor', f'{m}x{m}', 'apps',
                                     f'{self.icon_name}.png')
        if s > m:
            if os.path.isfile(max_size_dest):
                return
            logging.info(f'Icon size {s}x{s} is too large, resizing')
            new_img = img.resize((m, m), resample=Image.LANCZOS)
            img.close()
            img = new_img
            s = m
            save_direct = False
        if img.format != 'PNG':
            save_direct = False
        dest = os.path.join(self.datadir, 'icons', 'hicolor', f'{s}x{s}', 'apps', f'{self.icon_name}.png')
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
            app_mainfest = vdf.load(amf)
            # TODO maybe check if game is actually installed?
            yield app_mainfest['AppState']['appid']


def create_desktop_data(steam_root, destdir=None, steam_cmd='xdg-open'):
    logging.info('Loading appinfo.vdf')
    appinfo_data = {}
    with open(os.path.join(steam_root, 'appcache', 'appinfo.vdf'), 'rb') as af:
        _, apps_gen = appcache.parse_appinfo(af)
        for app in apps_gen:
            appinfo_data[app['appid']] = app['data']['appinfo']

    logging.info('Searching library folders')
    library_folders = []
    with open(os.path.join(steam_root, 'steamapps', 'libraryfolders.vdf'), 'r') as lf:
        for k, v in vdf.load(lf)['LibraryFolders'].items():
            if k.isdigit():
                library_folders.append(v)

    if destdir is None:
        destdir = os.path.join(os.environ.get('HOME'), '.local', 'share')

    for library_folder in library_folders:
        logging.info(f'Processing library {library_folder}')
        for app_id in get_installed_apps(library_folder):
            app = SteamApp(steam_root=steam_root, app_id=app_id, app_info=appinfo_data[int(app_id)])
            if not app.is_game():
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
