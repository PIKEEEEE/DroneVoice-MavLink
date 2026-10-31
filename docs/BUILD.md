# 빌드 방법

## Raspberry Pi

`src/receiver` 자체가 설치 소스입니다. 이 폴더 전체를 Pi로 복사한 뒤 `bash install.sh` 또는 `bash update.sh`를 실행하세요. `.sh`의 LF 줄바꿈을 유지해야 합니다.

## Windows 송신 플러그인과 설치기

필요한 환경: Windows, .NET SDK, .NET Framework 4.8, 설치된 Mission Planner. NuGet 패키지 최초 복원에는 인터넷이 필요합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .uild.ps1 -MissionPlannerDir 'C:\Program Files (x86)\Mission Planner'
```

`build.ps1`은 플러그인을 빌드하고, 생성된 DLL 7개의 해시와 대상 Mission Planner 버전을 설치기 소스에 반영한 뒤 단일 설치 EXE를 만듭니다. 출력은 `build/`에 생성됩니다. 릴리스 EXE는 관리자 권한을 요청하며, 테스트용 빌드는 별도로 생성합니다. Mission Planner 본체/참조 DLL은 저장소에 포함하지 않습니다.

```powershell
# 테스트용 설치 엔진을 실제 설치 폴더가 아닌 임시 폴더에서 검사
$env:DRONEVOICE_MP_DIR = 'C:\Program Files (x86)\Mission Planner'
.uild\SetupTest.exe --self-test "$PWDuild	est-install"
```

같은 임시 폴더에서 테스트를 다시 실행할 때는 이전 테스트 상태가 남지 않도록 새 경로를 지정하세요. 실제 프로그램 폴더를 테스트 경로로 지정하지 마세요.

동봉된 기존 EXE는 어셈블리 버전 1.3.9384.38250에 고정된 배포본입니다. 빌드 스크립트로 다른 설치 버전에 맞춰 재빌드할 수 있지만 실사용 검증은 별도입니다.
