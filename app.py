"""
오디오북 제작기 v3.0
─────────────────────────────────────────
실행: python -m streamlit run app.py
─────────────────────────────────────────
소설별 프로젝트 관리 + 화자별 독립 목소리
"""

import streamlit as st
import streamlit.components.v1
import re, io, wave, time, json, os, pickle, random
import lameenc

# 환경 자동 감지: Streamlit Cloud = /home/appuser
IS_CLOUD = os.environ.get('HOME', '') == '/home/appuser' 
from google import genai
from google.genai import types

# ═══════════════════════════════════════════
# 상수
# ═══════════════════════════════════════════
SAMPLE_RATE     = 24000
MAX_CHUNK_CHARS = 900   # TTS 1회 호출당 최대 글자수 (길수록 뒷부분에 잡음/에코 발생 위험 ↑)
SEED_BASE       = 7     # 생성 시 seed 고정 → 목소리 톤이 매 호출마다 랜덤하게 튀는 것을 완화 (구글 TTS의 알려진 불안정성)
CONFIG_FILE     = "config.json"

# 남성/여성 목소리 분리
MALE_VOICES = ["Charon", "Fenrir"]
FEMALE_VOICES = ["Kore", "Aoede"]
ALL_VOICES = MALE_VOICES + FEMALE_VOICES

MALE_VOICE_HELP   = "Charon=차분·깊음 | Fenrir=강함 | Orus=중성 | Puck=가벼움"
FEMALE_VOICE_HELP = "Kore=감성 | Aoede=서사적 | Zephyr=부드러움 | Leda=따뜻함"

# 프리셋 템플릿
# 모든 유형: [M]=내레이터+남자  [W]=여자  (2목소리)
PRESETS = {
    "일반소설": {
        "desc": "[M] 내레이터+남자  [W] 여자",
        "speakers": {
            "M": "Charon",
            "W": "Kore",
        },
        "tags": "title, narration, warm, calm, serious, emotional, soft, proud, nostalgic, longing, bright, concerned, sad, cheerful, playful, firm, surprised, honest, kind, gentle, teasing, awkward, shy, curious, cold, mysterious, excited, passionate, seductive, breathless, tense, commanding, dignified, formal, sorrowful, earnest, lamenting, resolute, tender, pleading, joyful, mournful, husky, panting, ecstatic, moaning, comforting, strained, crying_out, groaning, climaxing, shouting, inviting, whisper, urgent"
    },
    "고전": {
        "desc": "[M] 내레이터+남자  [W] 여자",
        "speakers": {
            "M": "Charon",
            "W": "Kore",
        },
        "tags": "title, narration, warm, calm, serious, emotional, soft, proud, nostalgic, longing, bright, concerned, sad, cheerful, playful, firm, surprised, honest, kind, gentle, teasing, awkward, shy, curious, cold, mysterious, excited, passionate, seductive, breathless, tense, commanding, dignified, formal, sorrowful, earnest, lamenting, resolute, tender, pleading, joyful, mournful, husky, panting, ecstatic, moaning, comforting, strained, crying_out, groaning, climaxing, shouting, inviting, whisper, urgent"
    },
    "성인": {
        "desc": "[M] 내레이터+남자  [W] 여자",
        "speakers": {
            "M": "Charon",
            "W": "Kore",
        },
        "tags": "title, narration, warm, calm, serious, emotional, soft, proud, nostalgic, longing, bright, concerned, sad, cheerful, playful, firm, surprised, honest, kind, gentle, teasing, awkward, shy, curious, cold, mysterious, excited, passionate, seductive, breathless, tense, commanding, dignified, formal, sorrowful, earnest, lamenting, resolute, tender, pleading, joyful, mournful, husky, panting, ecstatic, moaning, comforting, strained, crying_out, groaning, climaxing, shouting, inviting, whisper, urgent"
    },

}


# ═══════════════════════════════════════════
# 설정 저장/로드
# ═══════════════════════════════════════════
def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))
            # 기존 프로젝트 화자를 M/W 로 자동 정리
            for proj in cfg.get("projects", {}).values():
                spk = proj.get("speakers", {})
                m_voice = spk.get("M", spk.get("NA", spk.get("NARRATOR", "Charon")))
                w_voice = spk.get("W", "Kore")
                proj["speakers"] = {"M": m_voice, "W": w_voice}
            return cfg
        except:
            pass
    return {"api_key": "", "projects": {}, "current_project": ""}

def save_config(data: dict):
    json.dump(data, open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)



# ═══════════════════════════════════════════
# 원고 품질 검사
# ═══════════════════════════════════════════

ANALYSIS_PROMPT = """당신은 한국 소설 원고를 검토하는 전문 편집자입니다.

## 소설 배경 정보 (이 정보를 반드시 판단 기준으로 사용하세요)
- 시대 배경: {era}
- 문체 스타일: {style}

## 판단 기준
- 시대 배경에 맞는 단어/표현은 오류로 처리하지 마세요
  (예: 1970년대면 "국민학교", "전화교환수" 등은 정확한 표현)
- 문체 스타일이 "고어/사극체"면 하오체, 예스러운 표현은 오류 아님
- 문체 스타일이 "방언 포함"이면 사투리 표현은 오류 아님
- 문체 스타일이 "구어체"면 맞춤법보다 자연스러운 말투 우선

## 검사 항목
1. 어색한 문장: 자연스럽지 않은 표현, 어색한 어휘, 문장 흐름 문제
2. AI 작성 패턴: AI가 자주 쓰는 상투적 표현, 과도하게 정형화된 문장, 반복되는 구조
3. 맞춤법/문법: 철자 오류, 문법 오류, 띄어쓰기 (시대/문체 기준 적용)

반드시 아래 JSON 형식으로만 출력하세요 (마크다운 없이):
{{
  "issues": [
    {{
      "original": "원고에서 정확히 찾을 수 있는 텍스트",
      "suggestion": "수정 제안",
      "type": "어색함",
      "reason": "이유"
    }}
  ],
  "summary": "전체 분석 요약 (2~3줄)"
}}

type은 반드시 "어색함", "AI패턴", "맞춤법" 중 하나만 사용.
issues가 없으면 빈 배열 [] 반환.

원고:
{manuscript}"""


def analyze_manuscript(api_key: str, manuscript: str, model: str,
                       era: str = "현대", style: str = "표준 현대어") -> dict:
    """원고 품질 분석"""
    import json as _json
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=ANALYSIS_PROMPT.format(
            manuscript=manuscript, era=era, style=style)
    )
    text = response.text.strip()
    text = re.sub(r"```json|```", "", text).strip()
    try:
        return _json.loads(text)
    except:
        return {"issues": [], "summary": "분석 결과를 파싱할 수 없습니다."}

# ═══════════════════════════════════════════
# 태그 변환 프롬프트
# ═══════════════════════════════════════════
def build_tag_prompt(speakers: dict, tags: str = "") -> str:
    has_na = "NA" in speakers
    speaker_lines = []
    for spk in speakers:
        if spk == "NA":
            speaker_lines.append("- [M] : 내레이션(지문·묘사) + 모든 남자 대화")
        elif spk == "M":
            if has_na:
                speaker_lines.append("- [M]  : 주인공 외 모든 남자 대화")
            else:
                speaker_lines.append("- [M]  : 내레이션(지문·묘사) + 모든 남자 대화")
        elif spk == "W":
            speaker_lines.append("- [W]  : 모든 여자 대화")
        else:
            speaker_lines.append(f"- [{spk}] : {spk} 대화")

    speaker_section = "\n".join(speaker_lines)

    if not tags:
        tags = "title, narration, warm, calm, serious, emotional, soft, nostalgic, longing, bright, sad, cheerful, firm, surprised, honest, kind, gentle, playful"
    return f"""당신은 한국 소설 원고에 TTS 태그를 추가하는 전문가입니다.

## 절대 규칙
- 원고 텍스트를 절대 수정·요약·생략하지 마세요
- 원고 내용 전체를 빠짐없이 출력하세요
- 태그만 각 줄 앞에 추가하세요
- 구분선(________) 은 태그 없이 그대로 출력하세요

## 태그 형식
[화자] [감정] 텍스트

## 화자 종류
{speaker_section}

## 처리 방법
1. 챕터 제목 (예: 1장. 제목 / Chapter 1) → [NA] [title]
2. 내레이션·지문·묘사 → [NA] [narration]
3. 대화문("...") → 앞뒤 문맥으로 화자 판단
4. 내레이션과 대화가 섞인 문단 → 반드시 분리

## 감정 태그 목록 (이 목록에서만 선택, 다른 태그 절대 사용 금지)
{tags}

지금 바로 아래 원고 전체에 태그를 추가하세요:

{{manuscript}}"""


# ═══════════════════════════════════════════
# 핵심 함수
# ═══════════════════════════════════════════
def convert_tags(api_key, manuscript, model, speakers, tags=""):
    client = genai.Client(api_key=api_key)
    prompt = build_tag_prompt(speakers, tags)
    response = client.models.generate_content(
        model=model,
        contents=prompt.format(manuscript=manuscript)
    )
    text = response.text
    text = re.sub(r"```[a-z]*\n?", "", text).strip()
    return text


def normalize_tags(text: str) -> str:
    """[NARRATOR]/[NA] → [M] 자동 통일 (2인 화자 기준)"""
    text = text.replace("[NARRATOR]", "[M]")
    text = text.replace("[NA]", "[M]")
    return text

def parse_tagged_script(text):
    """순서 유지하며 태그줄 + 구분선 모두 파싱"""
    text = normalize_tags(text)
    lines = []
    tag_pattern = re.compile(r'^\[([A-Za-z가-힣]+)\]\s*\[([^\]]+)\]\s*(.+)$')
    sep_pattern  = re.compile(r'^[-_═=─]{3,}\s*$')
    for raw_line in text.split('\n'):
        stripped = raw_line.strip()
        if not stripped:
            continue
        # 구분선 감지
        if sep_pattern.match(stripped):
            lines.append({'speaker': 'PAUSE', 'emotion': 'pause', 'text': ''})
            continue
        # 태그 라인 감지
        m = tag_pattern.match(stripped)
        if m:
            speaker, emotion, content = m.groups()
            lines.append({
                'speaker': speaker.strip(),
                'emotion': emotion.strip(),
                'text':    content.strip()
            })
    return lines


def group_into_segments(lines):
    if not lines:
        return []
    segments = []
    cur_spk = lines[0]['speaker']
    cur_lines = [lines[0]]
    for line in lines[1:]:
        # PAUSE는 항상 독립 세그먼트
        if line['emotion'] == 'pause' or cur_lines[-1].get('emotion') == 'pause':
            segments.append({'speaker': cur_spk, 'lines': cur_lines,
                             'is_title': any(l['emotion'] == 'title' for l in cur_lines),
                             'is_pause': cur_lines[-1].get('emotion') == 'pause'})
            cur_spk = line['speaker']
            cur_lines = [line]
        elif line['speaker'] == cur_spk:
            cur_lines.append(line)
        else:
            segments.append({'speaker': cur_spk, 'lines': cur_lines,
                             'is_title': any(l['emotion'] == 'title' for l in cur_lines),
                             'is_pause': False})
            cur_spk = line['speaker']
            cur_lines = [line]
    segments.append({'speaker': cur_spk, 'lines': cur_lines,
                    'is_title': any(l['emotion'] == 'title' for l in cur_lines),
                    'is_pause': cur_lines[-1].get('emotion') == 'pause'})
    return segments


def get_voice_for_speaker(spk, speakers):
    """화자에 맞는 목소리 반환 - M/W 두 개만 사용"""
    if spk == "W" and "W" in speakers:
        return speakers["W"]
    # M, NA, NARRATOR, 기타 모두 → M 목소리
    return speakers.get("M", "Charon")


def merge_segments_by_voice(segs, speakers):
    """같은 목소리 연속 세그먼트 병합 → API 호출 감소"""
    if not segs:
        return []
    merged = []
    for seg in segs:
        voice = get_voice_for_speaker(seg['speaker'], speakers)
        if (merged and
            get_voice_for_speaker(merged[-1]['speaker'], speakers) == voice and
            not merged[-1]['is_title'] and not seg['is_title']):
            merged[-1]['lines'].extend(seg['lines'])
        else:
            merged.append({
                'speaker': seg['speaker'],
                'lines': list(seg['lines']),
                'is_title': seg['is_title']
            })
    return merged


def chunk_segment(segment_lines, max_chars=MAX_CHUNK_CHARS):
    chunks, current, current_len = [], [], 0
    for line in segment_lines:
        size = len(line['text']) + len(line['emotion']) + 25
        if current_len + size > max_chars and current:
            chunks.append(current)
            current, current_len = [line], size
        else:
            current.append(line)
            current_len += size
    if current:
        chunks.append(current)
    return chunks


def build_single_speaker_script(lines, voice_hint=""):
    """TTS 스크립트 생성 (pause 제외)"""
    parts = []
    for line in lines:
        if line.get('emotion') == 'pause':
            continue
        if line['emotion'] in ('narration', 'title'):
            parts.append(line['text'])
        else:
            parts.append(line['text'])
    return "\n".join(parts)


def call_tts_single(client, script, voice_name, tts_model, retry=3, status=None, seed=None):
    """항상 bytes를 반환하거나 예외를 던짐 — 절대 None을 반환하지 않음.
    (이전 버전은 rate-limit 재시도가 outer for-range를 다 써버리면 루프가 그냥
    끝나버려 암묵적으로 None을 반환하는 버그가 있었음 — pcm_list에 None이 섞여
    나중에 merge_to_mp3에서 TypeError로 터짐)"""
    rate_limit_retries = 0
    other_retries = 0
    config_kwargs = dict(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice_name
                )
            )
        )
    )
    if seed is not None:
        config_kwargs["seed"] = seed
    while True:
        try:
            response = client.models.generate_content(
                model=tts_model,
                contents=script,
                config=types.GenerateContentConfig(**config_kwargs)
            )
            if (response.candidates and
                response.candidates[0].content and
                response.candidates[0].content.parts and
                response.candidates[0].content.parts[0].inline_data):
                return to_pcm_bytes(response.candidates[0].content.parts[0].inline_data.data)
            else:
                return generate_silence(0.5)
        except Exception as e:
            msg = str(e)
            is_rate_limit = "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()
            is_server_err = ("500" in msg or "503" in msg or "INTERNAL" in msg
                              or "UNAVAILABLE" in msg or "DEADLINE_EXCEEDED" in msg)
            if (is_rate_limit or is_server_err) and rate_limit_retries < 10:
                rate_limit_retries += 1
                wait_s = 60 if is_rate_limit else 15
                reason = "API 분당 요청 제한에 걸림" if is_rate_limit else "구글 서버 일시 오류"
                if status is not None:
                    status.markdown(f"⏳ {reason}. {wait_s}초 대기 후 재시도 ({rate_limit_retries}/10)...")
                time.sleep(wait_s)
                continue
            other_retries += 1
            if other_retries < retry:
                time.sleep(3)
                continue
            raise e


PROGRESS_FILE = "progress.pkl"

def save_progress(pcm_list, done, chapter, chunk_meta=None):
    """진행상황을 파일로 저장"""
    with open(PROGRESS_FILE, 'wb') as f:
        pickle.dump({'pcm_list': pcm_list, 'done': done, 'chapter': chapter,
                     'chunk_meta': chunk_meta or []}, f)

def load_progress():
    """저장된 진행상황 로드"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'rb') as f:
                return pickle.load(f)
        except:
            pass
    return None

def clear_progress():
    """진행상황 파일 삭제"""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


def generate_silence(seconds):
    return bytes(int(SAMPLE_RATE * seconds) * 2)


def to_pcm_bytes(data):
    """TTS 응답의 오디오 데이터를 항상 순수 bytes로 변환.
    google-genai 버전/환경에 따라 str(base64)나 bytearray로 올 수 있어 방어적으로 처리."""
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(data, str):
        import base64
        return base64.b64decode(data)
    raise TypeError(f"예상치 못한 TTS 오디오 데이터 타입: {type(data)}")


def merge_to_wav(pcm_list):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for pcm in pcm_list:
            wf.writeframes(pcm)
    return buf.getvalue()


def merge_to_mp3(pcm_list):
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    mp3_data = encoder.encode(b"".join(pcm_list))
    return mp3_data + encoder.flush()


def pcm_duration_seconds(pcm_list):
    total_bytes = sum(len(p) for p in pcm_list)
    return total_bytes / 2 / SAMPLE_RATE


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}시간 {m}분 {s}초"
    if m:
        return f"{m}분 {s}초"
    return f"{s}초"


def count_total_chunks(lines):
    total = 0
    for seg in group_into_segments(lines):
        total += len(chunk_segment(seg['lines']))
    return total


# ═══════════════════════════════════════════

# ══════════════════════════════════════════
# 페이지 설정 & CSS
# ══════════════════════════════════════════
st.set_page_config(page_title="오디오북 메이커", page_icon="🎧", layout="wide")

# ── 리셋 플래그 처리 (모든 위젯 생성 전에 실행) ──
if st.session_state.pop('_pending_reset', False):
    for _k in ['manuscript_checked','tagged_script','audio_data',
                'analysis_result','analysis_text','accepted_fixes',
                'direct_input_mode','issue_filter','audio_gen_seconds',
                'pcm_list','chunk_meta']:
        st.session_state.pop(_k, None)
    st.session_state['manuscript']            = ""
    st.session_state['chapter_name']       = ""
    st.session_state['project_name_input'] = ""

# ── 업로드 파일명 → 챕터명 자동 입력 (위젯 생성 전에 처리) ──
if '_pending_chapter_name' in st.session_state:
    st.session_state['chapter_name'] = st.session_state.pop('_pending_chapter_name')

st.markdown("""
<style>
/* 제목 링크 아이콘 숨기기 */
h1 a, h2 a, h3 a { display: none !important; }
[data-testid="stHeaderActionElements"] { display: none !important; }

/* 스텝 헤더 */
.step-box {
    background:#faf5ff;
    border:2px solid #7c3aed;
    border-left:6px solid #7c3aed;
    border-radius:8px;
    padding:10px 16px;
    margin:20px 0 8px 0;
}
/* 사이드바 섹션 박스 */
/* 사이드바 카드 */
.sb-card {
    border-radius:10px;
    margin-bottom:10px;
    box-shadow:0 2px 8px rgba(124,58,237,0.12);
    overflow:hidden;
    border:1px solid #e9d5ff;
}
.sb-card-header {
    padding:8px 12px;
    font-size:13px;
    font-weight:800;
    color:white;
    letter-spacing:-0.2px;
}
.sb-card-body {
    padding:8px 10px;
}
/* 섹션별 헤더 색상 */
.h-api    { background:linear-gradient(90deg,#7c3aed,#9f67f5); }
.h-proj   { background:linear-gradient(90deg,#0369a1,#0ea5e9); }
.h-voice  { background:linear-gradient(90deg,#065f46,#10b981); }
.h-novel  { background:linear-gradient(90deg,#92400e,#f59e0b); }
.h-model  { background:linear-gradient(90deg,#1e1b4b,#4338ca); }
[data-testid="stSidebar"] .stSelectbox { margin-top:-4px; }
[data-testid="stSidebar"] .stTextInput { margin-top:-4px; }
[data-testid="stSidebar"] .stSlider    { margin-top:-2px; }
[data-testid="stSidebar"] .stMultiSelect { margin-top:-4px; }
/* 퍼플 포인트 */
[data-testid="stSidebar"] { background:var(--secondary-background-color); }

/* 사이드바 입력칸 - 라이트/다크 모두 대응 */
[data-testid="stSidebar"] input[type="text"],
[data-testid="stSidebar"] input[type="password"] {
    border:1.5px solid #7c3aed !important;
    border-radius:6px !important;
}
[data-testid="stSidebar"] input[type="text"]:focus,
[data-testid="stSidebar"] input[type="password"]:focus {
    border:2px solid #4f46e5 !important;
    box-shadow:0 0 0 3px rgba(124,58,237,0.2) !important;
}
/* 셀렉트박스 테두리 */
[data-testid="stSidebar"] [data-baseweb="select"] > div {
    border:1.5px solid #7c3aed !important;
    border-radius:6px !important;
}
/* 카드 바디 - 다크모드 대응 (white 강제 제거) */
.sb-body {
    border-radius:0 0 8px 8px;
    padding:8px 10px;
    margin-bottom:6px;
}
</style>
""", unsafe_allow_html=True)

def step_header(num, title, subtitle=""):
    sub = f"<small style='color:#666'> — {subtitle}</small>" if subtitle else ""
    return f"<div class='step-box'><b>{num}. {title}</b>{sub}</div>"


# ══════════════════════════════════════════
# 사이드바
# ══════════════════════════════════════════
with st.sidebar:
    cfg = load_config()

    # ── 사이드바 헤더 ────────────────────
    st.markdown("""
    <div style='background:linear-gradient(135deg,#7c3aed,#9f5ff0);
                border-radius:8px;padding:10px 12px;margin-bottom:10px;text-align:center'>
        <div style='color:white;font-size:15px;font-weight:800;letter-spacing:-0.3px'>
            ⚙️ 필수 설정
        </div>
        <div style='color:#e9d5ff;font-size:11px;margin-top:3px'>
            아래 설정 후 원고를 입력하세요
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── API 설정 ─────────────────────────
    st.markdown("<div style='background:linear-gradient(90deg,#7c3aed,#9f67f5);border-radius:8px 8px 0 0;padding:8px 12px;margin-top:6px'><span style='color:white;font-size:13px;font-weight:800'>API설정. Gemini Api · 타인노출주의</span></div><div style='border:2px solid #7c3aed;border-top:none;border-radius:0 0 8px 8px;padding:8px 10px;margin-bottom:6px'>", unsafe_allow_html=True)
    if IS_CLOUD:
        api_key = st.text_input("", value="", type="password",
                                 placeholder="AIzaSy...", label_visibility="collapsed",
                                 key="api_key_input",
                                 help="Google AI Studio 무료 발급\nhttps://aistudio.google.com/apikey\n입력 키는 이 브라우저에만 저장됩니다 (서버에는 저장 안 됨)")
        # 서버에는 저장하지 않고, 이 브라우저의 localStorage에만 저장/복원
        streamlit.components.v1.html("""
        <script>
        (function() {
            const STORAGE_KEY = "audiobook_gemini_api_key";
            const doc = window.parent.document;
            const input = doc.querySelector('input[type="password"]');
            if (!input) return;
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved && !input.value) {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(input, saved);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                setTimeout(function() { input.blur(); }, 50);
            }
            if (!input._lsBound) {
                input._lsBound = true;
                input.addEventListener('change', function() {
                    if (input.value) {
                        localStorage.setItem(STORAGE_KEY, input.value);
                    } else {
                        localStorage.removeItem(STORAGE_KEY);
                    }
                });
            }
        })();
        </script>
        """, height=0)
    else:
        api_key = st.text_input("", value=cfg.get("api_key",""), type="password",
                                 placeholder="AIzaSy...", label_visibility="collapsed",
                                 key="api_key_input",
                                 help="Google AI Studio 무료 발급\nhttps://aistudio.google.com/apikey")
        if api_key != cfg.get("api_key",""):
            cfg["api_key"] = api_key
            save_config(cfg)
    st.markdown("</div></div>", unsafe_allow_html=True)

    # ── 프로젝트명 ───────────────────────
    st.markdown("<div style='background:linear-gradient(90deg,#0369a1,#0ea5e9);border-radius:8px 8px 0 0;padding:8px 12px;margin-top:6px'><span style='color:white;font-size:13px;font-weight:800;cursor:help' title='소설/프로젝트 제목 입력 | 오디오 파일명에 사용됨'>📁 책 제목</span></div><div style='border:2px solid #0369a1;border-top:none;border-radius:0 0 8px 8px;padding:8px 10px;margin-bottom:6px'>", unsafe_allow_html=True)
    proj_default = "" if IS_CLOUD else cfg.get("project_name","")
    project_name = st.text_input("", value=proj_default,
                                  placeholder="예: 봄의시작, 제1부",
                                  label_visibility="collapsed", key="project_name_input")
    st.markdown("</div>", unsafe_allow_html=True)

    selected_preset = "일반소설"

    # ── 성우 설정 ────────────────────────
    st.markdown("<div style='background:linear-gradient(90deg,#065f46,#10b981);border-radius:8px 8px 0 0;padding:8px 12px;margin-top:6px'><span style='color:white;font-size:13px;font-weight:800;cursor:help' title='남성(M)/여성(W) 목소리 선택 | 선택 후 특징 설명 표시'>🎙️ 성우 설정</span></div><div style='border:2px solid #065f46;border-top:none;border-radius:0 0 8px 8px;padding:8px 10px;margin-bottom:6px'>", unsafe_allow_html=True)
    saved_voices = cfg.get("voices", {"M":"Charon","W":"Kore"})
    m_def = saved_voices.get("M","Charon")
    w_def = saved_voices.get("W","Kore")
    col_m, col_w = st.columns(2)
    with col_m:
        st.caption("🔵 남성(M)")
        m_voice = st.selectbox("", MALE_VOICES,
                                index=MALE_VOICES.index(m_def) if m_def in MALE_VOICES else 0,
                                label_visibility="collapsed", key="voice_M",
                                help="Charon: 차분·깊음 (내레이터 추천)\nFenrir: 강하고 힘있음")
    with col_w:
        st.caption("🔴 여성(W)")
        w_voice = st.selectbox("", FEMALE_VOICES,
                                index=FEMALE_VOICES.index(w_def) if w_def in FEMALE_VOICES else 0,
                                label_visibility="collapsed", key="voice_W",
                                help="Kore: 감성적·따뜻함 (추천)\nAoede: 서사적·명확")

    # 성우 특징 설명 (선택된 성우 기준)
    voice_desc = {
        "Charon":"차분하고 깊은 목소리 · 내레이터 추천",
        "Fenrir":"강하고 힘있는 목소리 · 강한 캐릭터",
        "Orus":"중성적이고 안정적 · 무난한 선택",
        "Puck":"가볍고 젊은 느낌 · 경쾌한 캐릭터",
        "Kore":"감성적이고 따뜻함 · 여주인공 추천",
        "Aoede":"서사적이고 명확 · 진지한 캐릭터",
        "Zephyr":"부드럽고 자연스러움 · 일상적 대화",
        "Leda":"따뜻하고 친근 · 편안한 느낌",
    }
    col_md, col_wd = st.columns(2)
    with col_md:
        st.markdown(f"<div style='font-size:11px;font-weight:600;color:#6ee7b7;background:rgba(16,185,129,0.15);border:1px solid #6ee7b7;border-radius:4px;padding:4px 7px'>{voice_desc.get(m_voice,'')}</div>", unsafe_allow_html=True)
    with col_wd:
        st.markdown(f"<div style='font-size:11px;font-weight:600;color:#6ee7b7;background:rgba(16,185,129,0.15);border:1px solid #6ee7b7;border-radius:4px;padding:4px 7px'>{voice_desc.get(w_voice,'')}</div>", unsafe_allow_html=True)
    speakers = {"M": m_voice, "W": w_voice}
    if m_voice != m_def or w_voice != w_def:
        cfg["voices"] = speakers
        save_config(cfg)
    st.markdown("</div></div>", unsafe_allow_html=True)

    # ── 소설 설정 ────────────────────────
    st.markdown("<div style='background:linear-gradient(90deg,#92400e,#f59e0b);border-radius:8px 8px 0 0;padding:8px 12px;margin-top:6px'><span style='color:white;font-size:13px;font-weight:800;cursor:help' title='시대배경+문체 설정 | 품질검사 정확도 향상 | 예: 1978년 서울, 조선시대'>📖 소설 설정</span></div><div style='border:2px solid #92400e;border-top:none;border-radius:0 0 8px 8px;padding:8px 10px;margin-bottom:6px'>", unsafe_allow_html=True)
    novel_era = st.text_input("시대 배경", value=cfg.get("novel_era","현대"),
                               placeholder="예: 1978년 서울, 조선시대",
                               key="novel_era",
                               help="구체적일수록 품질검사 정확도↑\n\n예시:\n• 1978년 서울\n• 1980년 제주도\n• 조선시대 중기\n• 현대 2024년")
    if novel_era != cfg.get("novel_era","현대") and not IS_CLOUD:
        cfg["novel_era"] = novel_era
        save_config(cfg)
    novel_style_list = st.multiselect("문체 스타일",
        ["표준 현대어","고어/사극체","방언 포함","대화체"],
        default=["표준 현대어"], key="novel_style",
        help="복수 선택 가능\n표준 현대어: 일반 소설\n고어/사극체: 하오체 허용\n방언 포함: 사투리 허용\n대화체: 말하듯 쓴 글")
    novel_style = ", ".join(novel_style_list) if novel_style_list else "표준 현대어"
    st.markdown("</div></div>", unsafe_allow_html=True)

    # ── 모델 설정 (3개 통합) ─────────────
    st.markdown("<div style='background:linear-gradient(90deg,#1e1b4b,#4338ca);border-radius:8px 8px 0 0;padding:8px 12px;margin-top:6px'><span style='color:white;font-size:13px;font-weight:800;cursor:help' title='품질검사/태그변환/TTS 모델 선택 | Pro=고품질 | Flash=빠름·저비용'>⚙️ 모델 설정</span></div><div style='border:2px solid #1e1b4b;border-top:none;border-radius:0 0 8px 8px;padding:8px 10px;margin-bottom:6px'>", unsafe_allow_html=True)
    check_model = st.selectbox("🔍 품질검사",
        ["gemini-2.5-pro","gemini-2.5-flash"],
        index=0, key="check_model",
        help="Pro: 정확도 우선 (추천)\nFlash: 빠른 검사")
    tag_model = st.selectbox("🤖 태그변환",
        ["gemini-2.5-flash","gemini-2.5-pro"],
        index=0, key="tag_model",
        help="Flash: 빠르고 충분한 품질 (추천)\nPro: 더 정교한 태그")
    tts_model = st.selectbox("🔊 TTS 오디오",
        ["gemini-2.5-flash-preview-tts","gemini-2.5-pro-preview-tts"],
        index=0, key="tts_model",
        help="Flash: 빠름·저비용 (테스트용)\nPro: 고품질 (최종 제작용)")
    title_pause = st.slider("⏸️ 제목 후 무음", 0.5, 3.0, 1.5, 0.5,
                             format="%.1f초", key="title_pause",
                             help="챕터 제목 후 무음\n추천: 1.5초")
    max_chunk_chars = st.slider("🔊 TTS 청크 최대 글자수", 300, 4000, MAX_CHUNK_CHARS, 100,
                             key="max_chunk_chars",
                             help="TTS 1회 호출당 최대 글자수.\n"
                                  "길수록 뒷부분에 잡음·에코가 생길 위험이 커집니다 "
                                  "(구글 TTS 모델의 알려진 한계 — 1~2분 넘는 오디오부터 열화 시작).\n"
                                  "잡음이 나면 이 값을 낮춰서 다시 생성해보세요.\n추천: 900자 이하")
    st.markdown("</div></div>", unsafe_allow_html=True)

    # ── 사용 가이드 (접이식) ─────────────
    with st.expander("📖 사용 가이드", expanded=False):
        st.markdown("""
**🚀 빠른 시작**
1. API Key 입력 (사이드바)
2. 원고 붙여넣기
3. 품질 검사 → 수정
4. 태그 변환
5. 오디오 생성 → 다운로드

---
**🔑 API Key 발급**
[aistudio.google.com/apikey](https://aistudio.google.com/apikey)
구글 계정으로 무료 발급 가능

---
**📅 시대 배경 입력 예시**
- `1978년 서울` → 국민학교 허용
- `조선시대` → 사극 표현 허용
- `현대` → 표준 현대어 기준
- 문체는 복수 선택 가능 (예: 표준+대화체)

---
**🎙️ 성우 추천**
- 내레이터: Charon (남) / Kore (여)
- 강한 캐릭터: Fenrir
- 부드러운 캐릭터: Zephyr

---
**💡 팁**
- 품질검사 모델: Pro 사용 권장
- 태그변환: Flash로도 충분
- TTS: Flash로 먼저 테스트 후 Pro로 최종 제작
        """)


# ══════════════════════════════════════════
# 메인 헤더
# ══════════════════════════════════════════
col_title, col_reset = st.columns([5, 1])
with col_title:
    st.markdown("""
    <div style='margin-bottom:6px;display:grid;grid-template-columns:80px 1fr;align-items:center;gap:14px;max-width:500px'>
      <div style='width:80px;height:80px;position:relative;overflow:visible'>
        <div style='width:80px;height:80px;border-radius:50%;background:rgba(124,58,237,0.08)'></div>
        <div style='width:58px;height:58px;border-radius:50%;background:rgba(124,58,237,0.15);position:absolute;top:11px;left:11px'></div>
        <div style='width:36px;height:36px;border-radius:50%;background:#7c3aed;position:absolute;top:22px;left:22px;display:flex;align-items:center;justify-content:center'>
          <div style='width:16px;height:18px;background:white;border-radius:2px;position:relative;overflow:hidden'>
            <div style='width:3.5px;height:18px;background:#e9d5ff;position:absolute;left:0;top:0'></div>
            <div style='width:9px;height:1.5px;background:#7c3aed;position:absolute;left:5px;top:4px'></div>
            <div style='width:9px;height:1.5px;background:#7c3aed;position:absolute;left:5px;top:8px'></div>
            <div style='width:6px;height:1.5px;background:#7c3aed;position:absolute;left:5px;top:12px'></div>
          </div>
        </div>
      </div>
      <div>
        <div style='line-height:1.1'>
          <span style='font-size:32px;font-weight:800;color:#7c3aed'>오디오북</span>
          <span style='font-size:32px;font-weight:300;color:#1a1a2e'>메이커</span>
        </div>
        <div style='font-size:13px;color:#9f7aea;font-weight:500;margin-top:4px'>
          소설을 소리로 만드는 스튜디오
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    proj_display = f"**{project_name}**" if project_name else "*(프로젝트명 없음)*"
    st.caption(f"프로젝트: {proj_display}  |  M={m_voice} / W={w_voice}")
with col_reset:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 새로 시작", use_container_width=True):
        # 리셋 플래그 설정 → 다음 렌더링에서 위젯 생성 전에 처리됨
        st.session_state['_pending_reset'] = True
        st.rerun()

st.divider()

chapter_name = st.text_input("챕터명 (파일명용)", value="",
                              placeholder="예: chapter_01",
                              key="chapter_name", help="저장 파일명에 사용됩니다")

# ══════════════════════════════════════════
# STEP 1: 원고 입력 & 품질 검사
# ══════════════════════════════════════════
st.markdown(step_header("1", "원고 입력 & 품질 검사",
            "품질 검사 후 자동으로 2단계로 이동"), unsafe_allow_html=True)

# 파일 업로드
uploaded_file = st.file_uploader(
    "📂 파일 가져오기 (TXT, DOCX, PDF, HWP)",
    type=["txt","docx","pdf"],
    key="file_uploader",
    help="TXT, DOCX, PDF 파일을 직접 불러올 수 있습니다"
)
if uploaded_file:
    try:
        if uploaded_file.name.endswith('.txt'):
            file_text = uploaded_file.read().decode('utf-8', errors='ignore')
        elif uploaded_file.name.endswith('.docx'):
            from docx import Document as DocxDoc
            import io
            doc = DocxDoc(io.BytesIO(uploaded_file.read()))
            file_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        elif uploaded_file.name.endswith('.pdf'):
            import io
            try:
                import pymupdf
                pdf = pymupdf.open(stream=uploaded_file.read(), filetype="pdf")
                file_text = "\n".join([page.get_text() for page in pdf])
            except:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(uploaded_file.read()))
                file_text = "\n".join([p.extract_text() or "" for p in reader.pages])
        st.session_state['manuscript'] = file_text
        if st.session_state.get('_last_uploaded_name') != uploaded_file.name:
            st.session_state['_last_uploaded_name'] = uploaded_file.name
            st.session_state['_pending_chapter_name'] = os.path.splitext(uploaded_file.name)[0]
            st.rerun()
        st.success(f"✅ {uploaded_file.name} 불러오기 완료 ({len(file_text):,}자)")
    except Exception as e:
        st.error(f"❌ 파일 읽기 오류: {e}. pip install python-docx pypdf 를 실행해 주세요.")

# ── Google Docs 가져오기 (OAuth 계정 연동) ──
GOOGLE_TOKEN_FILE = "google_token.pickle"
GOOGLE_CLIENT_SECRET_FILE = "credentials.json"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]
WEB_REDIRECT_URI = "https://audiobook-makers.streamlit.app/"

def get_google_creds_local():
    creds = None
    if os.path.exists(GOOGLE_TOKEN_FILE):
        with open(GOOGLE_TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        from google.auth.transport.requests import Request
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CLIENT_SECRET_FILE, GOOGLE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(GOOGLE_TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return creds

def get_web_flow():
    from google_auth_oauthlib.flow import Flow
    client_config = {
        "web": {
            "client_id": st.secrets["google_client_id"],
            "client_secret": st.secrets["google_client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [WEB_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(client_config, scopes=GOOGLE_SCOPES, redirect_uri=WEB_REDIRECT_URI)

def get_google_creds_cloud():
    creds = st.session_state.get('google_creds')
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        st.session_state['google_creds'] = creds
        return creds
    if "code" in st.query_params:
        flow = get_web_flow()
        flow.fetch_token(code=st.query_params["code"])
        creds = flow.credentials
        st.session_state['google_creds'] = creds
        st.query_params.clear()
        return creds
    return None

def extract_doc_id(url_or_id):
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url_or_id)
    if m:
        return m.group(1)
    return url_or_id.strip()

def read_google_doc_text(creds, doc_id):
    from googleapiclient.discovery import build
    docs = build("docs", "v1", credentials=creds)
    doc = docs.documents().get(documentId=doc_id).execute()
    lines = []
    for elem in doc.get("body", {}).get("content", []):
        para = elem.get("paragraph")
        if not para:
            continue
        text = "".join(r.get("textRun", {}).get("content", "") for r in para.get("elements", []))
        if text.strip():
            lines.append(text.rstrip("\n"))
    return "\n".join(lines)

with st.expander("📄 Google Docs에서 가져오기"):
    if IS_CLOUD:
        if "google_client_id" not in st.secrets or "google_client_secret" not in st.secrets:
            st.warning("Streamlit Cloud의 Secrets에 `google_client_id`, `google_client_secret`을 등록해주세요.")
        else:
            creds = get_google_creds_cloud()
            if not creds:
                flow = get_web_flow()
                auth_url, _ = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
                st.link_button("🔑 구글 계정으로 로그인", auth_url)
            else:
                st.success("✅ 구글 계정 연동됨")
                doc_url = st.text_input("구글 문서 링크를 붙여넣으세요",
                    placeholder="https://docs.google.com/document/d/xxxxxxxx/edit", key="google_doc_url_cloud")
                if st.button("📥 이 문서 가져오기", key="google_doc_fetch_cloud"):
                    if not doc_url.strip():
                        st.warning("링크를 먼저 입력해주세요.")
                    else:
                        try:
                            doc_id = extract_doc_id(doc_url)
                            file_text = read_google_doc_text(creds, doc_id)
                            st.session_state['manuscript'] = file_text
                            st.success(f"✅ 불러오기 완료 ({len(file_text):,}자)")
                        except Exception as e:
                            st.error(f"❌ 문서 읽기 오류: {e}")
    else:
        if not os.path.exists(GOOGLE_CLIENT_SECRET_FILE):
            st.warning(f"`{GOOGLE_CLIENT_SECRET_FILE}` 파일이 없습니다. Google Cloud Console에서 OAuth 클라이언트(데스크톱 앱)를 만들고 "
                       f"다운로드한 JSON을 `{GOOGLE_CLIENT_SECRET_FILE}` 이름으로 이 폴더에 저장하세요.")
        else:
            doc_url = st.text_input("구글 문서 링크를 붙여넣으세요",
                placeholder="https://docs.google.com/document/d/xxxxxxxx/edit", key="google_doc_url")
            if st.button("📥 이 문서 가져오기"):
                if not doc_url.strip():
                    st.warning("링크를 먼저 입력해주세요.")
                else:
                    try:
                        creds = get_google_creds_local()
                        doc_id = extract_doc_id(doc_url)
                        file_text = read_google_doc_text(creds, doc_id)
                        st.session_state['manuscript'] = file_text
                        st.success(f"✅ 불러오기 완료 ({len(file_text):,}자)")
                    except Exception as e:
                        st.error(f"❌ 문서 읽기 오류: {e}")

manuscript = st.text_area("", height=250,
    placeholder="여기에 원고를 붙여넣거나 위에서 파일을 불러오세요...",
    label_visibility="collapsed", key="manuscript")

char_count = len(manuscript) if manuscript else 0
st.markdown(
    f"<p style='font-size:16px;font-weight:600;color:#7c3aed;margin:4px 0'>글자 수: {char_count:,}자</p>",
    unsafe_allow_html=True
)

if not api_key:
    st.markdown("""
    <div style='background:linear-gradient(135deg,#faf5ff,#f3e8ff);
                border:2px solid #7c3aed;border-radius:12px;
                padding:20px 24px;margin-bottom:16px'>
        <div style='font-size:18px;font-weight:800;color:#5b21b6;margin-bottom:12px'>
            👋 처음 오셨나요? 시작 방법을 안내해 드립니다
        </div>
        <table style='width:100%;border-collapse:collapse'>
            <tr>
                <td style='width:25%;padding:6px 8px;vertical-align:top'>
                    <div style='background:#7c3aed;color:white;border-radius:50%;
                                width:28px;height:28px;text-align:center;
                                line-height:28px;font-weight:800;font-size:14px;
                                display:inline-block'>1</div>
                    <div style='font-size:12px;font-weight:700;color:#5b21b6;margin-top:4px'>API Key 입력</div>
                    <div style='font-size:11px;color:#666;margin-top:2px'>
                        왼쪽 사이드바<br>🔑 필수 설정에서<br>Gemini API Key 입력
                    </div>
                </td>
                <td style='width:25%;padding:6px 8px;vertical-align:top'>
                    <div style='background:#7c3aed;color:white;border-radius:50%;
                                width:28px;height:28px;text-align:center;
                                line-height:28px;font-weight:800;font-size:14px;
                                display:inline-block'>2</div>
                    <div style='font-size:12px;font-weight:700;color:#5b21b6;margin-top:4px'>설정 입력</div>
                    <div style='font-size:11px;color:#666;margin-top:2px'>
                        프로젝트명·<br>성우·시대배경<br>선택
                    </div>
                </td>
                <td style='width:25%;padding:6px 8px;vertical-align:top'>
                    <div style='background:#7c3aed;color:white;border-radius:50%;
                                width:28px;height:28px;text-align:center;
                                line-height:28px;font-weight:800;font-size:14px;
                                display:inline-block'>3</div>
                    <div style='font-size:12px;font-weight:700;color:#5b21b6;margin-top:4px'>원고 입력</div>
                    <div style='font-size:11px;color:#666;margin-top:2px'>
                        소설 원고를<br>아래 입력창에<br>붙여넣기
                    </div>
                </td>
                <td style='width:25%;padding:6px 8px;vertical-align:top'>
                    <div style='background:#7c3aed;color:white;border-radius:50%;
                                width:28px;height:28px;text-align:center;
                                line-height:28px;font-weight:800;font-size:14px;
                                display:inline-block'>4</div>
                    <div style='font-size:12px;font-weight:700;color:#5b21b6;margin-top:4px'>오디오 생성</div>
                    <div style='font-size:11px;color:#666;margin-top:2px'>
                        품질검사→<br>태그변환→<br>오디오 완성
                    </div>
                </td>
            </tr>
        </table>
        <div style='margin-top:12px;padding-top:10px;border-top:1px solid #e9d5ff;
                    font-size:11px;color:#7c3aed'>
            🔑 API Key 무료 발급:
            <a href='https://aistudio.google.com/apikey' target='_blank'
               style='color:#7c3aed;font-weight:700'>
                aistudio.google.com/apikey
            </a>
            &nbsp;(구글 계정 필요)
        </div>
    </div>
    """, unsafe_allow_html=True)

col_q1, col_q2 = st.columns(2)
with col_q1:
    has_text = bool(manuscript and manuscript.strip())
    btn_label = "🔍 품질 검사 시작" if has_text else "✏️ 원고를 먼저 입력하세요"
    if st.button(btn_label, type="primary" if has_text else "secondary",
                 disabled=not (api_key and has_text), use_container_width=True):
        with st.status("🔍 원고 품질 분석 중...", expanded=True) as status:
            st.write("Gemini가 문장을 분석하고 있습니다. (30초~1분 소요)")
            try:
                result = analyze_manuscript(api_key, manuscript, check_model,
                                        era=novel_era, style=novel_style)
                st.session_state['analysis_result'] = result
                st.session_state['analysis_text'] = manuscript
                st.session_state['accepted_fixes'] = {}
                st.session_state.pop('manuscript_checked', None)
                issues_count = len(result.get('issues', []))
                status.update(label=f"✅ 분석 완료 — {issues_count}개 발견", state="complete")
            except Exception as e:
                status.update(label=f"❌ 오류 발생", state="error")
                st.error(f"❌ {e}")
with col_q2:
    if st.button("⏭️ 검사 건너뛰기",
                 disabled=not manuscript, use_container_width=True):
        st.session_state['manuscript_checked'] = manuscript
        st.session_state.pop('analysis_result', None)
        st.rerun()

# 품질 검사 결과
if 'analysis_result' in st.session_state and 'manuscript_checked' not in st.session_state:
    result = st.session_state['analysis_result']
    issues = result.get('issues', [])
    summary = result.get('summary', '')

    if summary:
        st.info(f"📊 {summary}")

    if not issues:
        st.success("✅ 문제없음! 아래 단계로 진행하세요.")
        st.session_state['manuscript_checked'] = st.session_state['analysis_text']
    else:
        types = [i.get('type','') for i in issues]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("전체", len(issues))
        c2.metric("어색함 🟡", types.count("어색함"))
        c3.metric("AI패턴 🔴", types.count("AI패턴"))
        c4.metric("맞춤법 🟠", types.count("맞춤법"))

        accepted  = st.session_state.get('accepted_fixes', {})
        color_map = {"어색함":"🟡","AI패턴":"🔴","맞춤법":"🟠"}

        # ── 필터 버튼 ──────────────────────────
        flt = st.session_state.get('issue_filter','전체')
        cnt_all  = len(issues)
        cnt_awk  = types.count("어색함")
        cnt_ai   = types.count("AI패턴")
        cnt_spell= types.count("맞춤법")

        f0,f1,f2,f3 = st.columns(4)
        if f0.button(f"전체 ({cnt_all})",
                     type="primary" if flt=='전체' else "secondary",
                     use_container_width=True, key="flt_all"):
            st.session_state['issue_filter']='전체'; st.rerun()
        if f1.button(f"어색함🟡 ({cnt_awk})",
                     type="primary" if flt=='어색함' else "secondary",
                     use_container_width=True, key="flt_awk"):
            st.session_state['issue_filter']='어색함'; st.rerun()
        if f2.button(f"AI패턴🔴 ({cnt_ai})",
                     type="primary" if flt=='AI패턴' else "secondary",
                     use_container_width=True, key="flt_ai"):
            st.session_state['issue_filter']='AI패턴'; st.rerun()
        if f3.button(f"맞춤법🟠 ({cnt_spell})",
                     type="primary" if flt=='맞춤법' else "secondary",
                     use_container_width=True, key="flt_spell"):
            st.session_state['issue_filter']='맞춤법'; st.rerun()

        # 현재 필터 유형 전체 제안 적용 버튼
        if flt != '전체':
            if st.button(f"✅ '{flt}' 전체 → 제안으로 일괄 적용",
                         use_container_width=True, key="apply_all_type"):
                for j, iss in enumerate(issues):
                    if iss.get('type') == flt:
                        accepted[j] = {
                            'type':'suggestion',
                            'text': iss.get('suggestion',''),
                            'original': iss.get('original','')
                        }
                st.session_state['accepted_fixes'] = accepted
                st.rerun()

        st.markdown("---")

        # ── 직접수정 자동저장 콜백 ─────────────
        def make_custom_cb(idx, orig_txt):
            def cb():
                val = st.session_state.get(f"custom_inp_{idx}", "")
                acc = st.session_state.get('accepted_fixes', {})
                if val.strip():
                    acc[idx] = {'type':'custom','text':val,'original':orig_txt}
                else:
                    acc.pop(idx, None)
                st.session_state['accepted_fixes'] = acc
            return cb

        # ── 이슈 목록 ──────────────────────────
        filtered = [(j, iss) for j, iss in enumerate(issues)
                    if flt == '전체' or iss.get('type') == flt]

        for i, issue in filtered:
            orig   = issue.get('original','')
            sugg   = issue.get('suggestion','')
            itype  = issue.get('type','')
            reason = issue.get('reason','')
            icon   = color_map.get(itype,"⚪")

            cur      = accepted.get(i, {})
            sel_type = cur.get('type', None)

            if sel_type == 'original':   status_txt = "📌 원본"
            elif sel_type == 'suggestion': status_txt = "✅ 제안"
            elif sel_type == 'custom':   status_txt = "✏️ 직접"
            else:                        status_txt = "⬜ 미선택"

            with st.expander(f"{icon} [{itype}]  {orig[:45]}  —  {status_txt}", expanded=True):
                st.caption(f"💡 {reason}")
                co, cs, cc = st.columns(3)

                # 원본 카드
                with co:
                    is_sel = sel_type == "original"
                    st.markdown(
                        f"<div style='background:{'#ebf8ff' if is_sel else '#fff5f5'};"
                        f"border:{'2px solid #2b6cb0' if is_sel else '1px solid #feb2b2'};"
                        f"border-radius:8px;padding:8px 8px 4px;font-size:13px'>"
                        f"<b style='color:#2d3748'>원본</b></div>",
                        unsafe_allow_html=True
                    )
                    st.text_area("",
                        value=orig,
                        height=80,
                        disabled=True,
                        label_visibility="collapsed",
                        key=f"orig_disp_{i}")
                    if st.button("👆 원본 선택", key=f"sel_o_{i}", use_container_width=True):
                        accepted[i] = {'type':'original','text':orig,'original':orig}
                        st.session_state['accepted_fixes'] = accepted
                        st.rerun()

                # 제안 카드 (수정 가능)
                with cs:
                    is_sel = sel_type == "suggestion"
                    st.markdown(
                        f"<div style='background:{'#f0fff4' if is_sel else '#f9fff9'};"
                        f"border:{'2px solid #276749' if is_sel else '1px solid #9ae6b4'};"
                        f"border-radius:8px;padding:8px 8px 4px;font-size:13px'>"
                        f"<b style='color:#2d3748'>제안</b> "
                        f"<span style='font-size:11px;color:#888'>(수정 가능)</span></div>",
                        unsafe_allow_html=True
                    )
                    sugg_edited = st.text_area("",
                        value=cur.get('text', sugg) if is_sel else sugg,
                        height=80,
                        label_visibility="collapsed",
                        key=f"sugg_inp_{i}")
                    if st.button("✅ 제안 선택", key=f"sel_s_{i}", use_container_width=True):
                        accepted[i] = {'type':'suggestion','text':sugg_edited,'original':orig}
                        st.session_state['accepted_fixes'] = accepted
                        st.rerun()

                # 직접 수정 카드 - 원본/제안과 동일 디자인
                with cc:
                    is_sel   = sel_type == "custom"
                    cust_val = cur.get('text','') if is_sel else ''
                    st.markdown(
                        f"<div style='background:{'#fffbeb' if is_sel else '#fff'};"
                        f"border:{'2px solid #d97706' if is_sel else '1px solid #fde68a'};"
                        f"border-radius:8px;padding:8px 8px 4px;font-size:13px'>"
                        f"<b style='color:#2d3748'>직접 수정</b></div>",
                        unsafe_allow_html=True
                    )
                    cust_input = st.text_area("",
                        value=cust_val,
                        height=80,
                        placeholder="직접 입력...",
                        label_visibility="collapsed",
                        key=f"custom_inp_{i}")
                    if st.button("✏️ 직접수정 선택", key=f"sel_c_{i}", use_container_width=True):
                        if cust_input.strip():
                            accepted[i] = {'type':'custom','text':cust_input,'original':orig}
                            st.session_state['accepted_fixes'] = accepted
                            st.rerun()

        st.markdown("---")
        applied = len(accepted)
        total   = len(issues)
        if st.button(f"✅ 검사 완료 → 다음 단계  ({applied}/{total}개 선택됨)",
                     type="primary", use_container_width=True):
            final = st.session_state['analysis_text']
            for idx, fix in accepted.items():
                if fix.get('type') in ('suggestion','custom'):
                    final = final.replace(fix['original'], fix['text'], 1)
            st.session_state['manuscript_checked'] = final
            st.rerun()


# ══════════════════════════════════════════
# STEP 2: 검사 완료 원고
# ══════════════════════════════════════════
if 'manuscript_checked' in st.session_state:
    st.markdown(step_header("2", "검사 완료 원고",
                "저장 후 태그 변환으로 진행"), unsafe_allow_html=True)

    checked = st.session_state['manuscript_checked']
    st.text_area("", value=checked, height=200,
                 label_visibility="collapsed", key="checked_display")
    checked_count = len(checked)
    st.markdown(
        f"<p style='font-size:16px;font-weight:600;color:#7c3aed;margin:4px 0'>글자 수: {checked_count:,}자</p>",
        unsafe_allow_html=True
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("⬇️ 원고 저장",
            data=checked.encode("utf-8"),
            file_name=f"{project_name}_{chapter_name}_원고.txt",
            mime="text/plain", use_container_width=True)
    with c2:
        if st.button("🔄 태그 변환 시작", type="primary",
                     disabled=not api_key, use_container_width=True):
            with st.status("🤖 태그 변환 중...", expanded=True) as status:
                st.write("Gemini가 화자와 감정 태그를 분석합니다. (30초~1분 소요)")
                try:
                    tags = PRESETS.get(selected_preset, PRESETS["일반소설"]).get("tags","")
                    tagged = convert_tags(api_key, checked, tag_model, speakers, tags)
                    st.session_state['tagged_script'] = normalize_tags(tagged)
                    st.session_state.pop('audio_data', None)
                    status.update(label="✅ 태그 변환 완료", state="complete")
                except Exception as e:
                    status.update(label="❌ 오류 발생", state="error")
                    st.error(f"❌ {e}")
    with c3:
        if st.button("📋 태그 직접 입력", use_container_width=True):
            st.session_state['direct_input_mode'] = True
            st.session_state.pop('tagged_script', None)
            st.rerun()

    if st.session_state.get('direct_input_mode'):
        direct_text = st.text_area("태그 원고 붙여넣기", height=200,
            placeholder="[M] [narration] 텍스트...\n[W] [bright] 대사...",
            key="direct_input_text")
        cd1, cd2 = st.columns(2)
        with cd1:
            if st.button("✅ 확인", type="primary", use_container_width=True):
                if direct_text.strip():
                    st.session_state['tagged_script'] = normalize_tags(direct_text)
                    st.session_state['direct_input_mode'] = False
                    st.rerun()
        with cd2:
            if st.button("❌ 취소", use_container_width=True):
                st.session_state['direct_input_mode'] = False
                st.rerun()


# ══════════════════════════════════════════
# STEP 3: 오디오 태그 원고
# ══════════════════════════════════════════
if 'tagged_script' in st.session_state:
    st.markdown(step_header("3", "오디오 태그 원고",
                "수정 가능 · 저장 후 오디오 제작"), unsafe_allow_html=True)

    edited = st.text_area("", value=st.session_state['tagged_script'],
                           height=300, label_visibility="collapsed", key="edited_script")

    lines = parse_tagged_script(edited)
    if lines:
        sc = {}
        for l in lines:
            sc[l['speaker']] = sc.get(l['speaker'], 0) + 1
        cols = st.columns(min(len(sc), 5))
        for i, (spk, cnt) in enumerate(sc.items()):
            cols[i % len(cols)].metric(spk, cnt)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button("⬇️ 태그 원고 저장",
            data=edited.encode("utf-8"),
            file_name=f"{project_name}_{chapter_name}_태그.txt",
            mime="text/plain", use_container_width=True)
    with c2:
        st.caption(f"총 {len(lines)}줄")


# ══════════════════════════════════════════
# STEP 4: 오디오 제작
# ══════════════════════════════════════════
if 'tagged_script' in st.session_state:
    edited_text = st.session_state.get('edited_script', st.session_state['tagged_script'])
    lines = parse_tagged_script(edited_text)

    st.markdown(step_header("4", "오디오 제작",
                "생성 완료 후 자동으로 프로젝트명으로 저장"), unsafe_allow_html=True)

    segs_raw = group_into_segments(lines)
    segs = merge_segments_by_voice(segs_raw, speakers)
    total_chunks = sum(len(chunk_segment(s['lines'], max_chunk_chars)) for s in segs)
    saved_calls  = sum(len(chunk_segment(s['lines'], max_chunk_chars)) for s in segs_raw) - total_chunks
    st.caption(
        f"세그먼트 {len(segs_raw)}개 → 병합 {len(segs)}개  |  "
        f"청크 {total_chunks}개 (최대 {max_chunk_chars}자)  |  API 절감 {saved_calls}회  |  {tts_model}"
    )

    saved_prog  = load_progress()
    has_progress = saved_prog is not None and saved_prog.get('chapter') == chapter_name
    if has_progress:
        done_so_far = saved_prog.get('done', 0)
        st.warning(f"⏸️ 이전 작업: {done_so_far}/{total_chunks} 청크에서 중단됨")
        cb1, cb2 = st.columns(2)
        with cb1:
            start_btn = st.button("▶️ 이어서 생성", type="primary", use_container_width=True)
        with cb2:
            if st.button("🔄 처음부터", use_container_width=True):
                clear_progress()
                st.rerun()
        resume_from = done_so_far if start_btn else None
    else:
        start_btn = st.button("✅ 오디오 생성 시작", type="primary",
                              disabled=not (lines and api_key), use_container_width=True)
        resume_from = 0 if start_btn else None

    if resume_from is not None:
        gen_start  = time.time()
        client     = genai.Client(api_key=api_key)
        progress   = st.progress(0)
        status     = st.empty()
        pcm_list   = list((saved_prog or {}).get('pcm_list',[])) if resume_from > 0 else []
        chunk_meta = list((saved_prog or {}).get('chunk_meta',[])) if resume_from > 0 else []

        # 저장된 진행상황에 손상된(None 등) 청크가 섞여 있으면 그 지점부터 다시 생성
        for i, p in enumerate(pcm_list):
            if not isinstance(p, (bytes, bytearray)):
                pcm_list = pcm_list[:i]
                chunk_meta = chunk_meta[:i]
                resume_from = i
                st.warning(f"⚠️ 저장된 {i+1}번째 청크 데이터가 손상되어 그 지점부터 다시 생성합니다.")
                break

        error_flag = False
        done       = resume_from
        chunk_idx  = 0

        for seg in segs:
            voice = get_voice_for_speaker(seg['speaker'], speakers)
            chunks = chunk_segment(seg['lines'], max_chunk_chars)
            for chunk in chunks:
                if chunk_idx < resume_from:
                    chunk_idx += 1
                    continue
                chars = sum(len(l['text']) for l in chunk)
                status.markdown(
                    f"🎙️ **[{seg['speaker']}]** ({voice}) — "
                    f"{done+1}/{total_chunks} ({chars}자)"
                )
                progress.progress(done / total_chunks)
                try:
                    voice_hint = "남성" if seg['speaker'] == "M" else "여성"
                    script = build_single_speaker_script(chunk, voice_hint)
                    seed   = SEED_BASE + chunk_idx
                    pcm    = call_tts_single(client, script, voice, tts_model, status=status, seed=seed)
                    pcm_list.append(pcm)
                    chunk_meta.append({'kind':'audio', 'speaker':seg['speaker'],
                                        'voice':voice, 'script':script, 'seed':seed})
                    done += 1
                    chunk_idx += 1
                    save_progress(list(pcm_list), done, chapter_name, list(chunk_meta))
                except Exception as e:
                    st.error(f"❌ [{seg['speaker']}] {e}")
                    st.info(f"💾 {done}청크까지 저장됨. [▶️ 이어서 생성]으로 재시작하세요.")
                    error_flag = True
                    break
            if seg['is_title'] and not error_flag:
                pcm_list.append(generate_silence(title_pause))
                chunk_meta.append({'kind':'silence', 'seconds':title_pause})
            if error_flag:
                break

        if not error_flag and pcm_list:
            status.markdown("🔗 MP3로 합치는 중...")
            mp3 = merge_to_mp3(pcm_list)
            st.session_state['audio_data'] = mp3
            st.session_state['pcm_list'] = pcm_list
            st.session_state['chunk_meta'] = chunk_meta
            st.session_state['audio_gen_seconds'] = time.time() - gen_start
            clear_progress()
            progress.progress(1.0)
            status.markdown(f"🎧 완료! (소요시간 {format_duration(st.session_state['audio_gen_seconds'])})")

    if 'audio_data' in st.session_state:
        mp3  = st.session_state['audio_data']
        mb   = len(mp3) / 1024 / 1024
        fname = f"{project_name}_{chapter_name}.mp3"
        audio_len = pcm_duration_seconds(st.session_state.get('pcm_list', []))
        gen_seconds = st.session_state.get('audio_gen_seconds')
        gen_txt = f"  |  ⏱️ 제작 소요시간 {format_duration(gen_seconds)}" if gen_seconds is not None else ""
        st.success(f"✅ 완료 — {mb:.1f} MB  |  🎵 오디오 길이 {format_duration(audio_len)}{gen_txt}")
        st.audio(mp3, format="audio/mp3")
        st.download_button(f"⬇️ {fname} 저장",
            data=mp3, file_name=fname,
            mime="audio/mpeg", use_container_width=True)

        # ── 청크별 재생성 (구글 TTS가 가끔 목소리 톤을 다르게 내는 문제 대응) ──
        meta = st.session_state.get('chunk_meta') or []
        audio_indices = [i for i, m in enumerate(meta) if m['kind'] == 'audio']
        if audio_indices:
            with st.expander("🔁 특정 부분 다시 생성 — 목소리가 이상하게(다른 사람처럼) 나온 부분만 재시도"):
                st.caption(
                    "구글 TTS는 같은 목소리를 요청해도 가끔 톤·억양이 다르게 나오는 "
                    "알려진 불안정성이 있습니다. 전체를 다시 만들 필요 없이, "
                    "이상하게 나온 부분만 골라서 다시 생성할 수 있습니다."
                )
                labels = [
                    f"#{i+1}  [{meta[i]['speaker']}]  {meta[i]['script'][:30].replace(chr(10), ' ')}..."
                    for i in audio_indices
                ]
                pick = st.selectbox("다시 생성할 청크", options=list(range(len(audio_indices))),
                                     format_func=lambda k: labels[k], key="regen_chunk_pick")
                pick_idx = audio_indices[pick]
                m = meta[pick_idx]
                st.text_area("이 청크의 스크립트", value=m['script'], height=100,
                              disabled=True, key="regen_script_view")
                st.audio(merge_to_wav([st.session_state['pcm_list'][pick_idx]]), format="audio/wav")
                if st.button("🔁 이 부분만 다시 생성", key="regen_btn"):
                    with st.spinner("다시 생성 중..."):
                        regen_client = genai.Client(api_key=api_key)
                        new_seed = random.randint(0, 2_000_000_000)
                        new_pcm = call_tts_single(regen_client, m['script'], m['voice'], tts_model, seed=new_seed)
                    st.session_state['pcm_list'][pick_idx] = new_pcm
                    st.session_state['chunk_meta'][pick_idx]['seed'] = new_seed
                    st.session_state['audio_data'] = merge_to_mp3(st.session_state['pcm_list'])
                    st.success("재생성 완료! 위쪽 오디오가 갱신되었습니다.")
                    st.rerun()
