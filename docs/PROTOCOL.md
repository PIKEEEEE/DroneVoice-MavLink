# 송수신 프로토콜

MAVLink2 TUNNEL 메시지의 `payload_type=32768`을 사용합니다. 하나의 TUNNEL에 완전한 Opus 패킷 하나를 담으며 분할 전송은 구현하지 않습니다. `payload_length`는 헤더와 오디오를 합한 실제 길이입니다.

| 바이트 | 크기 | 내용 |
|---|---:|---|
| 0 | 1 | 버전 1 |
| 1~2 | 2 | uint16 시퀀스 번호, little endian |
| 3~6 | 4 | uint32 밀리초 타임스탬프, little endian |
| 7 | 1 | 플래그, bit 0 = PTT 종료 |
| 8 이후 | 0~120 | Opus 패킷 |

Python struct 형식은 `<BHIB`입니다. 시퀀스는 오디오 및 종료 패킷마다 증가하며 65535 이후 0으로 돌아갑니다. 일반 음성은 flags=0 / 20ms / 16kHz mono입니다. PTT 종료는 flags=1, payload_length=8이며 디코더에 넣지 않습니다. 타임스탬프는 메타데이터이고 재생 시각은 수신기의 monotonic clock과 시퀀스로 관리합니다.

디코더 출력 320 샘플을 상태를 유지하는 3배 FIR 변환으로 960 샘플/채널, 좌우 동일 stereo float32 little endian으로 변환합니다. 장치마다 독립 큐/재생 프로세스를 사용합니다. 지터 버퍼 누락 프레임은 libopus PLC로 보정합니다.

기본 수신 대상 SYS=1 / COMP=191, UART=/dev/serial0 / 115200입니다. 수신기는 1초마다 heartbeat를 전송합니다. Sender의 Target SYS/COMP가 다르면 비행컨트롤러가 TUNNEL을 해당 UART로 전달하지 않을 수 있습니다.
