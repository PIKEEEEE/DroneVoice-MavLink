# DroneVoiceHM30

Mission Planner의 PC 마이크 음성을 **MAVLink2 TUNNEL + Opus**로 보내고, Raspberry Pi에서 스피커로 재생하는 PTT 프로젝트입니다.

## 다운로드

| 용도 | 파일 |
|---|---|
| Windows 송신 플러그인 설치 | [설치 묶음](dist/windows/DroneVoice_MissionPlanner_Installer.zip) · [설치 EXE](dist/windows/DroneVoice_MissionPlanner_Setup.exe) |
| Raspberry Pi 신규 설치·업데이트 | [최신 자동 설치 묶음](dist/raspberry-pi/DroneVoice_AutoInstall.zip) |
| 개발 보고서 | [2026-10-28 PDF](docs/Development_Report_2026-10-28.pdf) |
| 변경 이력·검증 범위 | [개발 기록](docs/DEVELOPMENT.md) |
| 이전 배포본 | [이전 설치본 목록](dist/archive/) — 비교·복구용, 신규 설치에는 사용하지 마세요. |

## 구성

```text
PC 마이크 → Mission Planner PTT 플러그인 → MAVLink 통신 링크
 → 비행컨트롤러 TELEM2 → Raspberry Pi UART → Opus 디코딩
 → AUX / USB / Bluetooth / HDMI 직접 출력
```

- 음성: 16 kHz mono / Opus 12 kbps / 20 ms 프레임
- 출력: 48 kHz stereo float32
- UART: `/dev/serial0`, 115200 bps
- Target: **SYS=1, COMP=191**
- TUNNEL `payload_type=32768`, 8바이트 헤더, 패킷당 Opus 최대 120바이트
- 기본 지터 버퍼 80 ms, 손실 프레임 PLC, PTT 종료 신호 처리
- 약 2초마다 활성 출력 장치를 감지해 장치별로 직접 재생

## Windows 송신부 설치

1. Mission Planner를 완전히 종료합니다.
2. 설치 묶음을 풀고 `DroneVoice_MissionPlanner_Setup.exe`를 실행합니다.
3. 관리자 권한 확인 후 `MissionPlanner.exe`가 있는 폴더를 선택하고 **설치 / 업데이트**를 누릅니다.
4. Mission Planner를 열고 **Target SYS=1 / COMP=191**로 맞춥니다.
5. PTT 버튼 또는 스페이스 키를 누른 채 말합니다.

플러그인은 기존 Mission Planner 링크를 사용합니다. 별도 포트를 열지 않습니다. 기존 파일 백업 및 설치 전 상태 복원을 지원합니다.

**동봉 EXE는 Mission Planner 어셈블리 버전 `1.3.9384.38250` 기준**이며, 다른 버전은 설치를 중단합니다. 코드 서명이 없는 EXE입니다. 빌드/복원 검사는 통과했지만 새 PC의 실제 마이크/PTT 송신은 설치 후 확인해야 합니다.

## Raspberry Pi 수신부 설치

최신 자동 설치 ZIP을 풀고 해당 폴더에서 일반 사용자로 실행합니다. 앞에 `sudo`를 붙이지 마세요.

```bash
# 새 설치 또는 중단된 신규 설치 재시도
bash install.sh

# 이미 설치된 환경 업데이트
bash update.sh

# 완료 후 한 번 재부팅
sudo reboot
```

설치기는 의존성, UART 활성화, 시리얼 로그인 콘솔 해제, dialout 권한, 자동 로그인, 부팅 서비스 실행을 설정합니다. UART 질문/OK 창과 시작 엔터 대기는 없습니다. 관리자 비밀번호는 필요할 수 있습니다. 신규 설치에는 인터넷이 필요합니다.

부팅 설정은 `/boot/firmware` 또는 `/boot`에서 확인하고 원본을 `~/.local/share/dronevoice/system-backups`에 백업합니다. 기본 포트 `/dev/serial0`이 생기려면 재부팅이 필요합니다. Pi 5의 GPIO UART 등 보드별 다른 포트 매핑은 별도 확인이 필요합니다.

서비스 시작 또는 출력 장치 연결 시 해당 장치의 음소거를 해제하고 볼륨을 100%로 설정합니다. 사용 중 볼륨을 지속적으로 덮어쓰지는 않습니다. 스피커의 물리 볼륨은 별도일 수 있습니다.

```bash
systemctl --user status dronevoice.service --no-pager
sudo journalctl -b _SYSTEMD_USER_UNIT=dronevoice.service -n 20 -f -o cat

# ZIP을 푼 폴더에서 실시간 진단
bash diagnose.sh

# 실제 장치로 테스트음 출력
~/.local/share/dronevoice/.venv/bin/python ~/.local/share/dronevoice/control.py --test-audio
```

## 상태와 제한

- 사용자 환경에서 **AUX PTT 재생**, 단일 Bluetooth 직접 재생, 서비스 부팅 자동 시작을 확인했습니다.
- Bluetooth는 **재부팅 후 연결 해제·재연결이 필요할 수 있습니다**. OS 페어링·연결은 선행되어야 하며, Bluetooth 연결 자체의 자동 복구는 구현하지 않았습니다.
- 최종 자동 UART·최대 볼륨·질문창 제거 변경은 자체/모의 검사를 수행했습니다. 모든 Raspberry Pi OS 버전의 실제 설치 성공을 확인한 것은 아닙니다.
- 모든 스피커란 **OS에서 활성화된 물리 출력 장치**입니다. 비활성 카드 프로필을 켜지 않으며, 스피커 간 지연 차이는 있을 수 있습니다.
- `active (running)`만으로 실제 수신을 보장하지 않습니다. PTT 중 `AUDIO received/decoded`, `LINK tunnels`, `FILTER`, `peak`를 확인하세요.
- 제거 스크립트는 서비스/바로가기를 제거합니다. UART·자동 로그인·볼륨 등 OS 설정을 자동 복구하지는 않습니다.

## 소스와 빌드

- `src/sender/`: C# 송신 플러그인과 프로젝트
- `src/windows-installer/`: Windows 설치기 소스·관리자 권한 매니페스트·제3자 고지
- `src/receiver/`: 최신 Python 수신기 및 설치 스크립트
- `docs/`: 프로토콜, 개발 기록, PDF 보고서, 빌드 안내
- `dist/`: 최신 배포 파일과 명확히 구분한 이전 설치본

[Windows 재빌드 안내](docs/BUILD.md) · [패킷 사양](docs/PROTOCOL.md)

```bash
python3 src/receiver/receiver.py --self-test
python3 -m unittest discover -s tests -v
```

`dist/SHA256SUMS.txt`로 배포 파일 무결성을 확인할 수 있습니다. GitHub Actions나 자동 외부 배포는 설정하지 않았습니다.

## 제3자 소프트웨어

Mission Planner 본체는 배포하지 않습니다. NAudio·Concentus 및 포함된 Microsoft 라이브러리 고지는 [THIRD-PARTY-NOTICES](src/windows-installer/THIRD-PARTY-NOTICES.txt)를 확인하세요. 별도의 프로젝트 오픈소스 라이선스는 아직 지정하지 않았습니다.
