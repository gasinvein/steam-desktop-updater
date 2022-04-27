#!/usr/bin/env python3

from pathlib import Path, PurePosixPath, PureWindowsPath
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


class SteamIconReadError(Exception):
    def __str__(self):
        if self.__cause__ is not None:
            return str(self.__cause__)
        return super().__str__()


class DesktopFileParser(ConfigParser):
    def optionxform(self, option):
        return option


class SteamApp(object):
    def __init__(self, steam_root: Path, library_folder: Path, app_id: int, app_info: t.Mapping):
        self.app_id = app_id
        self.app_info = app_info
        self.steam_root = steam_root
        self.library_folder = library_folder
        self.desktop_name = f'steam_app_{app_id}'
        self.icon_name = f'steam_icon_{app_id}'

    @property
    def name(self):
        return self.app_info['common']['name']

    @property
    def is_game(self):
        if 'common' in self.app_info:
            if self.app_info['common']['type'].lower() == 'game':
                return True
        return False

    @property
    def is_installed(self) -> bool:
        installdir = self.app_info['config']['installdir']
        app_dir = self.library_folder / 'steamapps' / 'common' / installdir
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

    def get_desktop_entry(self, steam_cmd: str = DEFAULT_STEAM_CMD):
        return {
            'Desktop Entry': {
                'Type': 'Application',
                'Name': self.name,
                'Comment': 'Launch this game via Steam',
                'Exec': f'{steam_cmd} steam://rungameid/{self.app_id}',
                'Icon': self.icon_name,
                'Categories': 'Game;X-Steam;'
            }
        }

    def save_desktop_entry(self, destdir: Path, steam_cmd: str = DEFAULT_STEAM_CMD):
        app_desktop = DesktopFileParser()
        app_desktop.read_dict(self.get_desktop_entry(steam_cmd))
        app_desktop_file = destdir / 'applications' / f'{self.desktop_name}.desktop'
        app_desktop_file.parent.mkdir(parents=True, exist_ok=True)
        with app_desktop_file.open('w') as df:
            app_desktop.write(df, space_around_delimiters=False)

    def get_icon_files(self):
        """
        Get name of the file containing icon(s)
        """
        icon_containers = []
        common_info = self.app_info['common']
        icons_dir = self.steam_root / 'steam' / 'games'
        for icon_kind, icon_ext, container in [
            ('linuxclienticon', 'zip', SteamIconZip),
            ('clienticon', 'ico', SteamIconICO)
        ]:
            if icon_kind in common_info:
                icon_hash = common_info[icon_kind]
                logging.debug('%s is set, searching it...', icon_kind)
                icon_path = icons_dir / f'{icon_hash}.{icon_ext}'
                if icon_path.is_file():
                    logging.debug('found %s', icon_path)
                    icon_containers.append(container(icon_path, self.icon_name))
        return icon_containers

    def extract_icons(self, destdir: Path):
        for icon_file in self.get_icon_files():
            logging.info('Extracting icon(s) from %s', icon_file.path)
            try:
                with icon_file:
                    icon_file.extract(destdir)
                return
            except SteamIconReadError as err:
                logging.error("Failed to read Steam icon container %s: %s", icon_file.path, err)
                continue
        logging.warning('No usable icons found')


class SteamIconContainer(object):
    def __init__(self, icon_file: Path, icon_name: str):
        self.path = icon_file
        self.icon_name = icon_name
        self.file = None

    def __enter__(self):
        raise NotImplementedError

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.file.close()

    def get_dest(self, size: int, datadir: Path):
        destdir = datadir / 'icons' / 'hicolor' / f'{size}x{size}' / 'apps'
        if not destdir.is_dir():
            destdir.mkdir(parents=True)
        return destdir / f'{self.icon_name}.png'

    def extract(self, datadir: Path):
        raise NotImplementedError


class SteamIconZip(SteamIconContainer):
    def __enter__(self):
        try:
            self.file = zipfile.ZipFile(self.path, 'r')
        except zipfile.BadZipFile as err:
            raise SteamIconReadError from err
        return self

    def extract(self, datadir: Path):
        assert isinstance(self.file, zipfile.ZipFile)
        for zi in self.file.infolist():
            if zi.is_dir() or not zi.filename.lower().endswith('.png'):
                continue
            logging.debug('Saving icon %s', zi.filename)
            with self.file.open(zi.filename) as img_file:
                try:
                    img = Image.open(img_file)
                except OSError as e:
                    logging.warning('%s: %s', self.path.name, e)
                    continue
                h, w = img.size
                assert h == w
                assert img.format == 'PNG'
                img_file.seek(0)
                with open(self.get_dest(h, datadir), 'wb') as d:
                    d.write(img_file.read())
                img.close()


class SteamIconICO(SteamIconContainer):
    def __enter__(self):
        try:
            self.file = Image.open(self.path)
        except OSError as err:
            raise SteamIconReadError from err
        return self

    def extract(self, datadir: Path):
        assert isinstance(self.file, Image.Image)
        assert self.file.format == 'ICO'
        for size in self.file.ico.sizes():
            subimg = self.file.ico.getimage(size)
            h, w = subimg.size
            assert h == w
            if subimg.size != size:
                logging.warning('Expected size %ix%i, got %ix%i', size, size, h, w)
            logging.debug('Saving icon size %ix%i', h, w)
            subimg.save(self.get_dest(h, datadir), format='PNG')


class SteamInstallation(object):
    steam_root: Path
    _appinfo: t.Dict[int, t.Dict]
    _appinfo_fp: t.Optional[t.IO]
    _appinfo_header: t.Optional[t.Dict[str, t.Any]]
    _appinfo_reader: t.Optional[t.Generator[t.Dict[str, t.Any], None, None]]

    def __init__(self, steam_root: Path):
        self.steam_root = steam_root.resolve()
        self._appinfo = {}
        self._appinfo_fp = None
        self._appinfo_header = None
        self._appinfo_reader = None

    def __enter__(self):
        self._appinfo_fp = (self.steam_root / 'appcache' / 'appinfo.vdf').open('rb')
        self._appinfo_header, self._appinfo_reader = appcache.parse_appinfo(self._appinfo_fp)
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self._appinfo_header = None
        self._appinfo_reader = None
        self._appinfo_fp.close()
        self._appinfo_fp = None

    @property
    def library_folders(self) -> t.List[Path]:
        def _getpath(folder_obj: t.Union[str, dict]) -> Path:
            if isinstance(folder_obj, dict):
                return Path(folder_obj["path"])
            if isinstance(folder_obj, str):
                return Path(folder_obj)
            raise TypeError(folder_obj)
        with (self.steam_root / 'steamapps' / 'libraryfolders.vdf').open('r') as lf:
            loaded_vdf_dict = {k.lower(): v for k, v in vdf.load(lf).items()}
        library_folders = loaded_vdf_dict["libraryfolders"]
        return [_getpath(v).resolve() for k, v in library_folders.items() if k.isdigit()]

    def read_appinfo(self, app_id: int) -> t.Dict[str, t.Any]:
        if app_id not in self._appinfo:
            logging.info('Searching appinfo.vdf for app ID %i', app_id)
            assert self._appinfo_reader is not None
            # Resume appcache.parse_appinfo generator from where we stopped previously;
            # read apps until we find needed ID, caching all other apps along the way
            for app_appinfo in self._appinfo_reader:
                if app_appinfo['appid'] not in self._appinfo:
                    self._appinfo[app_appinfo['appid']] = app_appinfo
                if app_appinfo['appid'] == app_id:
                    break
        return self._appinfo[app_id]

    def read_installed_apps(self) -> t.Iterable[SteamApp]:
        """
        Enumerate IDs of installed apps in given library
        """
        for folder_path in {self.steam_root} | set(self.library_folders):
            logging.info('Collecting apps in folder %s', folder_path)
            for appmanifest_path in (folder_path / 'steamapps').glob('appmanifest_*.acf'):
                with appmanifest_path.open('r') as amf:
                    appmanifest = vdf.load(amf)
                app_state = {k.lower(): v for k, v in appmanifest['AppState'].items()}
                app_id = int(app_state['appid'])
                app_appinfo = self.read_appinfo(app_id)
                yield SteamApp(self.steam_root,
                               folder_path,
                               app_id,
                               app_appinfo['data']['appinfo'])


def create_desktop_data(steam_root: Path, destdir: Path = None, steam_cmd: str = DEFAULT_STEAM_CMD):
    if destdir is None:
        destdir = Path('~/.local/share').expanduser()

    with SteamInstallation(steam_root) as steam:
        steam_apps = list(steam.read_installed_apps())

    for app in steam_apps:
        if not app.is_game:
            continue
        if not app.is_installed:
            continue
        logging.info('Processing app ID %s : %s', app.app_id, app.name)
        app.save_desktop_entry(destdir, steam_cmd)
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
