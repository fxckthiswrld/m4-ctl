# Momentum 4 Control (M4)

Управление Sennheiser Momentum 4 из под Windows. Шумоподавление, режимы ANC, прозрачность и anti-wind
по Bluetooth Classic (SPP).

## Возможности

| Команда | Что делает |
|---|---|
| `list` | Показать сопряжённые Bluetooth-устройства |
| `anc on` / `anc off` | Включить / выключить шумоподавление |
| `mode adaptive` | Режим «Адаптив» (ANC подстраивается автоматически) |
| `mode anti_wind` | Режим «Anti-Wind» |
| `mode comfort` | Режим «Comfort» |
| `mode off` | Выключить ANC |
| `custom` | Режим «Кастом» (полный ANC) |
| `transparency 0..100` | Слайдер Кастома: 0 = ANC 100%, 100 = полная прозрачность, 50 = середина |
| `antiwind 0/1/2` | Anti-Wind: `0` off, `1` MAX, `2` AUTO |
| `get` | Прочитать текущее состояние наушников |

## Требования

- Python 3.10+
- Windows 10/11
- [uv](https://docs.astral.sh/uv/) — менеджер окружений и зависимостей

### Установка
```
git clone https://github.com/fxckthiswrld/m4-ctl.git
```
```
uv sync
```

Запуск команд — через `uv run`:

```
uv run python m4_ctl.py list
```

## Использование

Сначала найдите адрес наушников:

```
uv run python m4_ctl.py list
```

Пример вывода:
```
AA:BB:CC:DD:EE:FF  MOMENTUM 4
```

Дальше все команды принимают `--addr`:

```bash
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF list          # показать устройства
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF anc on        # шумоподавление вкл
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF anc off       # шумоподавление выкл
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF mode adaptive # режим «Адаптив»
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF custom        # режим «Кастом» (полный ANC)
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF transparency 100   # полная прозрачность
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF transparency 50    # середина
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF transparency 0     # полный ANC в кастоме
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF antiwind 1    # anti-wind MAX
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF antiwind 0    # anti-wind off
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF get           # состояние наушников
```

## Как это работает

Momentum 4 управляется по **Classic Bluetooth SPP (RFCOMM)**, а не по BLE.
Протокол — **GAIA3** (vendor `0x0495`), сервис `a2129ff3-081b-4c45-8afe-469d9c4842ec`.

SPP-кадр GAIA3:

```
FF 03 00 <len> | <vendor 2 байта BE> | <cmd 2 байта BE> | <payload len байт>
```

Полезные команды:

| Команда | Назначение |
|---|---|
| `0x1A00 [mode, state]` | Режим ANC (mode: 1=anti-wind, 2=comfort, 3=adaptive) |
| `0x1A02 [level]` | Слайдер Кастома (0..100) |
| `0x1A04 [0/1]` | ANC вкл/выкл |
| `0x1804 [0/1]` | Прозрачность (TransparentHearing) вкл/выкл |
| `0x1A05` / `0x1A01` / `0x1A03` / `0x1805` | Чтение состояния |

Протокол был восстановлен анализом JS-бандла официального приложения
Sennheiser Smart Control.

## Структура проекта

```
m4_ctl.py            CLI: команды + GAIA3-фрейминг
gaia_transport.py    Транспорт SPP: Windows (winrt) / macOS (IOBluetooth)
pyproject.toml       Зависимости под ОС (uv)
```

## Важно

- Наушник закрывает SPP-соединение после каждого ответа — скрипт автоматически
  переподключается между командами.
- Слайдер Кастома (`0x1A02`) работает только вне режима «Адаптив» — скрипт сам
  выходит из него перед отправкой.
- Проект неофициальный, создан путём реверс-инжиниринга. Используйте на свой
  страх и риск. Протокол восстановлен эмпирически, возможны отличия между
  версиями прошивки.

## License

MIT
