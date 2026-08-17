# Momentum 4 Control

Неофициальное приложение для управления Sennheiser Momentum 4 по Classic
Bluetooth SPP (RFCOMM) и протоколу GAIA3.

Поддерживаются Windows и macOS.

## Возможности

- Поиск сопряжённых Bluetooth-устройств и подключение к Momentum 4.
- ANC: Adaptive, Custom, Comfort и Off.
- Anti-Wind: Off, Max и Auto.
- Настройка прозрачности в Custom.
- CLI на Python и desktop-приложение Electron.

## Требования для разработки

- Python 3.10+ и [uv](https://docs.astral.sh/uv/).
- Node.js 18+ и npm для Electron UI.
- Сопряжённые с компьютером Momentum 4.

Готовые standalone-релизы уже содержат Python-мост. Пользователям Windows и
macOS не нужны Python и `uv`.

## Быстрый старт

```bash
git clone https://github.com/fxckthiswrld/m4-ctl.git
cd m4-ctl
uv sync
```

Найдите адрес наушников:

```bash
uv run python m4_ctl.py list
```

Пример команды:

```bash
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF mode adaptive
```

Доступные команды CLI:

```text
list
anc on|off
mode adaptive|comfort|anti_wind|off
custom
antiwind 0|1|2
transparency 0..100
get
```

`transparency 0` соответствует максимальному ANC в Custom, `100` - максимальной
прозрачности.

## Desktop UI

```bash
cd ui
npm ci
npm run dev
```

Electron запускает `bridge.py` автоматически. В окне приложения выберите
наушники, подключитесь и настройте режимы.

## Standalone-сборки

Сборка должна выполняться на целевой ОС: PyInstaller не собирает macOS-бинарник
на Windows и наоборот.

### Windows

```powershell
cd ui
npm ci
npm run dist:win
```

Артефакты: `ui/release/*.exe`.

### macOS

На Mac установите `uv` и Node.js, затем:

```bash
uv sync
cd ui
npm ci
npm run dist:mac
```

Артефакты: `ui/release/*.dmg` и `ui/release/*.zip`.

Для Apple Silicon собирайте на Apple Silicon Mac, для Intel - на Intel Mac.
macOS может показать предупреждение при запуске, пока приложение не подписано и
не notarized Apple.

## Релиз

1. Поднимите версию в `pyproject.toml`, `ui/package.json` и `ui/package-lock.json`.
2. На Windows выполните `npm run dist:win`, на Mac - `npm run dist:mac`.
3. Проверьте приложения на машинах без Python и `uv`.
4. Закоммитьте исходники и lock-файлы, но не `ui/release` и `build`.
5. Создайте тег и GitHub Release, затем прикрепите `.exe`, `.dmg` и `.zip`.

Пример:

```bash
git status
git add -u
git commit -m "release: v0.2.1"
git push origin master
git tag -a v0.2.1 -m "Release v0.2.1"
git push origin v0.2.1
gh release create v0.2.1 --title "v0.2.1" --generate-notes
```

## Технически

Momentum 4 использует GAIA3 с vendor `0x0495` и RFCOMM-сервисом
`a2129ff3-081b-4c45-8afe-469d9c4842ec`.

- `m4_ctl.py` - CLI.
- `bridge.py` - JSON Lines-мост для Electron.
- `gaia_transport.py` - SPP-транспорт: WinRT на Windows и IOBluetooth на macOS.

## Предупреждение

Проект не связан с Sennheiser. Протокол восстановлен эмпирически и может
отличаться между версиями прошивки. Используйте на свой страх и риск.

## License

MIT
