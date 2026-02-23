# Lama Cleaner Video GUI (한국어)

Release에서 받으세요: [GUI Release ZIP](https://github.com/david419kr/lama-cleaner-video-gui/releases/download/v0.1.0/gui-release-v0.1.0.zip)

기존 CLI 스크립트 버전은 `legacy/cli-script` 브랜치에 보관되어 있습니다.

`lama-cleaner`를 사용해 동영상 워터마크/오브젝트를 구간 단위로 제거하는 Windows 네이티브 GUI 앱입니다.

현재 리포지토리는 GUI 중심이며, 기존 배치/PowerShell 파이프라인 방식은 사용하지 않습니다.

<img width="1202" height="1070" alt="image" src="https://github.com/user-attachments/assets/819e5d1e-204e-4dec-a9ee-a75845707623" />

## 주요 기능

- 프레임 단위 타임라인 작업
  - 좌/우 방향키 프레임 이동
  - 길게 누르면 가속 이동
  - 시크바 호버 미리보기
- 프레임/시간 기반 Segment 편집
- Segment별 마스크 처리
  - 외부 마스크 파일 지정
  - 내장 마스크 에디터(브러시, 지우개, 사각형, 원형)로 직접 그리기
- 프리뷰 내 마스크 시각화
  - 타임라인 구간 색상 표시
  - 마스크 활성 배지 + 빨간 오버레이
- GUI에서 `lama-cleaner` 멀티 인스턴스 제어 (`8080`부터 순차 할당)
- 처리 일시정지/재개 및 작업 상태 복구

## 시스템 요구사항

- Windows 10/11 (x64)
- NVIDIA GPU + CUDA 사용 가능한 드라이버 (`--device=cuda` 사용)
- 최초 설정 시 인터넷 연결 (Python/pip/패키지/모델 다운로드)

## 빠른 시작

리포지토리 루트에서 실행:

```bat
start_gui.bat
```

### `start_gui.bat` 동작

최초 실행(또는 런타임 누락 시) 자동으로 다음을 수행합니다.

1. Embedded Python `3.10.11` 다운로드/압축해제 (`.runtime/python310`)
2. Embedded Python의 `site-packages` 활성화
3. `get-pip.py`로 `pip` 설치
4. `requirements.txt` 기반 GUI 의존성 설치
5. `lama-cleaner` 런타임 검증 또는 설치
   - `torch==2.10.0`, `torchvision==0.25.0`, `torchaudio==2.10.0` (`cu128` 인덱스)
   - `lama-cleaner`
   - 호환성 고정을 위해 `huggingface_hub==0.14.1` 강제 핀
6. `lama-cleaner` 실행 파일 검증
7. Embedded Python으로 `main.py` 실행

이미 환경이 유효하면 설치 단계는 자동으로 스킵됩니다.

## GUI 사용 순서

1. 비디오 불러오기
   - `Browse Video` 또는 앱 창으로 드래그앤드롭
2. Segment 구간 설정
   - `Start Frame`, `End Frame` 직접 입력
   - 또는 `Set Start = Current Frame`, `Set End = Current Frame`
   - `Add Segment` 클릭
3. Segment별 마스크 설정
   - 폴더 아이콘: 마스크 파일 지정
   - 연필 아이콘: 마스크 직접 그리기
   - 빨간 `X`: 해당 Segment 삭제
4. `Instances` 설정 후 `Apply Instance Count` 클릭
5. `Start Processing` 클릭

## Segment / 마스크 처리 규칙

- Segment는 서로 겹치면 안 됩니다.
- 프레임 처리 방식:
  - Segment 내부 + 마스크 있음: `lama-cleaner` 인페인팅
  - Segment 내부 + 마스크 없음: 원본 프레임 복사(스킵)
  - Segment 외부: 원본 프레임 복사
- 내장 마스크 에디터 저장 결과는 black/white 바이너리 PNG입니다.
- 외부 마스크 파일도 바이너리 형태를 권장합니다.

## 처리 상태 모델

- 실행 중 `Start Processing` 버튼은 `Pause Processing`으로 전환됩니다.
- `Cancel`은 현재 작업을 취소합니다.
- 일시정지 시:
  - 진행 중인 요청을 안전하게 마무리
  - `workspace/paused_job.json`에 재개 정보 저장
  - 앱 재실행 시 자동 복구되고 `Resume Processing`으로 표시

## 멀티 인스턴스 동작

- 인스턴스 수 범위: `1`~`8`
- 시작 포트: `8080`
- 순차 포트 할당: `8080`, `8081`, `8082`, ...
- 인스턴스 설정값은 `workspace/ui_settings.json`에 저장
- 앱 시작 시 설정 개수만큼 자동 기동, 종료 시 관리 중 인스턴스 정리

## 성능 참고

- 프레임 추출:
  - CUDA 디코드 경로 우선
  - 실패 시 CPU 경로 fallback
- 프레임 머지:
  - `h264_nvenc` 우선
  - 실패 시 `libx264` fallback
- 마스크 적용 프레임은 활성 `lama-cleaner` 포트들에 균등 분배되어 병렬 처리됩니다.

## 워크스페이스 구조

- `workspace/jobs/job-*/input`: 추출 프레임
- `workspace/jobs/job-*/output`: 정리/복사된 프레임
- `workspace/jobs/job-*/video_cleaned.mp4`: 오디오 머지 전 비디오
- `workspace/masks/`: 생성된 마스크/레퍼런스 프레임
- `workspace/lama_logs/`: `lama-cleaner` 시작/런타임 로그
- `workspace/ui_settings.json`: UI 설정
- `workspace/paused_job.json`: 일시정지/재개 메타데이터

## 트러블슈팅

- `Failed to start lama-cleaner automatically`
  - `workspace/lama_logs/*.log` 확인
  - 최초 실행은 모델 초기화/다운로드로 시간이 오래 걸릴 수 있음
- `Cannot start lama-cleaner on port 8080`
  - 해당 포트를 다른 프로세스가 사용 중
  - 점유 프로세스를 종료하거나 인스턴스 수 조정
- `No lama-cleaner instances running`
  - `Apply Instance Count`를 먼저 클릭
- `ffmpeg not found` / `ffprobe not found`
  - `ffmpeg/bin/ffmpeg.exe`, `ffmpeg/bin/ffprobe.exe` 존재 여부 확인

## 초기화(클린 리셋)

로컬 런타임/작업 상태를 완전히 초기화하려면:

1. 앱 종료
2. `.runtime/` 삭제
3. `workspace/` 삭제
4. `start_gui.bat` 재실행
