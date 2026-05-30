"""
오디오북 제작기 v3.0
─────────────────────────────────────────
실행: python -m streamlit run app.py
─────────────────────────────────────────
소설별 프로젝트 관리 + 화자별 독립 목소리
"""

import streamlit as st
import streamlit.components.v1
import re, io, wave, time, json, os, pickle
from google import genai
from google.genai import types

# ═══════════════════════════════════════════
# 상수
# ═══════════════════════════════════════════
SAMPLE_RATE     = 24000
MAX_CHUNK_CHARS = 4000
CONFIG_FILE     = "config.json"

# 남성/여성 목소리 분리
MALE_VOICES = ["Charon", "Fenrir", "Orus", "Puck", "Schedar", "Gacrux"]
FEMALE_VOICES = ["Kore", "Aoede", "Zephyr", "Leda", "Zubenelgenubi", "Achernar"]
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

아래 원고를 꼼꼼히 분석하여 3가지를 검사하세요:
1. 어색한 문장: 자연스럽지 않은 표현, 어색한 어휘, 문장 흐름 문제
2. AI 작성 패턴: AI가 자주 쓰는 상투적 표현, 과도하게 정형화된 문장, 반복되는 구조
3. 맞춤법/문법: 철자 오류, 문법 오류, 띄어쓰기

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


def analyze_manuscript(api_key: str, manuscript: str, model: str) -> dict:
    """원고 품질 분석"""
    import json as _json
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=ANALYSIS_PROMPT.format(manuscript=manuscript)
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
    text = normalize_tags(text)
    lines = []
    pattern = re.compile(r'^\[([A-Za-z가-힣]+)\]\s*\[([^\]]+)\]\s*(.+)$', re.MULTILINE)
    for match in pattern.finditer(text):
        speaker, emotion, content = match.groups()
        lines.append({'speaker': speaker.strip(), 'emotion': emotion.strip(), 'text': content.strip()})
    return lines


def group_into_segments(lines):
    if not lines:
        return []
    segments = []
    cur_spk = lines[0]['speaker']
    cur_lines = [lines[0]]
    for line in lines[1:]:
        if line['speaker'] == cur_spk:
            cur_lines.append(line)
        else:
            segments.append({'speaker': cur_spk, 'lines': cur_lines,
                             'is_title': any(l['emotion'] == 'title' for l in cur_lines)})
            cur_spk = line['speaker']
            cur_lines = [line]
    segments.append({'speaker': cur_spk, 'lines': cur_lines,
                    'is_title': any(l['emotion'] == 'title' for l in cur_lines)})
    return segments


def get_voice_for_speaker(spk, speakers):
    """화자에 맞는 목소리 반환"""
    if spk in speakers:
        return speakers[spk]
    elif spk == "NA" and "M" in speakers:
        return speakers["M"]
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


def chunk_segment(segment_lines):
    chunks, current, current_len = [], [], 0
    for line in segment_lines:
        size = len(line['text']) + len(line['emotion']) + 25
        if current_len + size > MAX_CHUNK_CHARS and current:
            chunks.append(current)
            current, current_len = [line], size
        else:
            current.append(line)
            current_len += size
    if current:
        chunks.append(current)
    return chunks


def build_single_speaker_script(lines, voice_hint=""):
    """목소리 일관성 지시문 포함한 TTS 스크립트 생성"""
    parts = []
    # 목소리 일관성 앵커 (청크마다 동일한 톤 유지)
    if voice_hint:
        parts.append(f"[{voice_hint} 목소리로 일관되게 읽어주세요]")
    for line in lines:
        if line['emotion'] in ('narration', 'title'):
            parts.append(line['text'])
        else:
            parts.append(f"({line['emotion']}) {line['text']}")
    return "\n".join(parts)


def call_tts_single(client, script, voice_name, tts_model, retry=3):
    for attempt in range(retry):
        try:
            response = client.models.generate_content(
                model=tts_model,
                contents=script,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_name
                            )
                        )
                    )
                )
            )
            if (response.candidates and
                response.candidates[0].content and
                response.candidates[0].content.parts and
                response.candidates[0].content.parts[0].inline_data):
                return response.candidates[0].content.parts[0].inline_data.data
            else:
                return generate_silence(0.5)
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(3)
            else:
                raise e


PROGRESS_FILE = "progress.pkl"

def save_progress(pcm_list, done, chapter):
    """진행상황을 파일로 저장"""
    with open(PROGRESS_FILE, 'wb') as f:
        pickle.dump({'pcm_list': pcm_list, 'done': done, 'chapter': chapter}, f)

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


def merge_to_wav(pcm_list):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for pcm in pcm_list:
            wf.writeframes(pcm)
    return buf.getvalue()


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
                'direct_input_mode','issue_filter']:
        st.session_state.pop(_k, None)
    st.session_state['manuscript']            = ""
    st.session_state['chapter_name']       = ""
    st.session_state['project_name_input'] = ""

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
.sb-box {
    background:#ffffff;
    border:2px solid #7c3aed;
    border-radius:10px;
    padding:12px 14px;
    margin-bottom:12px;
}
.sb-title {
    font-size:13px;
    font-weight:700;
    color:#7c3aed;
    margin-bottom:8px;
    padding-bottom:6px;
    border-bottom:1px solid #e9d5ff;
}
/* 퍼플 포인트 */
[data-testid="stSidebar"] { background:#fdfaff; }
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

    # ── 1. API 설정 (자동저장) ───────────
    st.markdown("<div class='sb-box'><div class='sb-title'>🔑 API 설정</div>",
                unsafe_allow_html=True)
    api_key = st.text_input("Gemini API Key", value="",
                             type="password", placeholder="AIzaSy...",
                             key="api_key_input")
    st.caption("🔑 매번 입력 필요 · 타인에게 노출되지 않습니다")
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 2. 프로젝트명 (자동저장) ──────────
    st.markdown("<div class='sb-box'><div class='sb-title'>📁 프로젝트명</div>",
                unsafe_allow_html=True)
    project_name = st.text_input("", value="",
                                  placeholder="예: 제1부_봄의시작",
                                  label_visibility="collapsed", key="project_name_input")
    st.markdown("</div>", unsafe_allow_html=True)

    # 소설 유형은 태그에 영향 없으므로 고정값 사용
    selected_preset = "일반소설"

    # ── 3. 성우 설정 (자동저장 + 수평정렬) ─
    st.markdown("<div class='sb-box'><div class='sb-title'>🎙️ 성우 설정</div>",
                unsafe_allow_html=True)
    saved_voices = cfg.get("voices", {"M":"Charon","W":"Kore"})
    m_def = saved_voices.get("M","Charon")
    w_def = saved_voices.get("W","Kore")

    st.markdown("""
    <table style='width:100%;border-collapse:collapse'>
      <tr>
        <td style='width:50%;padding:0 4px 4px 0;font-size:12px;font-weight:600;color:#1a56db'>
          🔵 남성(M)
        </td>
        <td style='width:50%;padding:0 0 4px 4px;font-size:12px;font-weight:600;color:#e02424'>
          🔴 여성(W)
        </td>
      </tr>
    </table>""", unsafe_allow_html=True)

    col_m, col_w = st.columns(2)
    with col_m:
        m_voice = st.selectbox("", MALE_VOICES,
                                index=MALE_VOICES.index(m_def) if m_def in MALE_VOICES else 0,
                                label_visibility="collapsed", key="voice_M")
    with col_w:
        w_voice = st.selectbox("", FEMALE_VOICES,
                                index=FEMALE_VOICES.index(w_def) if w_def in FEMALE_VOICES else 0,
                                label_visibility="collapsed", key="voice_W")

    col_mh, col_wh = st.columns(2)
    with col_mh:
        st.markdown(
            "<div style='font-size:13px;color:#555;line-height:1.6'>"
            "Charon=차분<br>Fenrir=강함<br>Orus=중성<br>Puck=가벼움"
            "</div>", unsafe_allow_html=True)
    with col_wh:
        st.markdown(
            "<div style='font-size:13px;color:#555;line-height:1.6'>"
            "Kore=감성<br>Aoede=서사<br>Zephyr=부드러움<br>Leda=따뜻함"
            "</div>", unsafe_allow_html=True)

    speakers = {"M": m_voice, "W": w_voice}
    if m_voice != m_def or w_voice != w_def:
        cfg["voices"] = speakers
        save_config(cfg)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 5. 태그 변환 모델 (자동저장) ─────
    st.markdown("<div class='sb-box'><div class='sb-title'>🤖 태그 변환 모델</div>",
                unsafe_allow_html=True)
    tag_model = st.radio("", ["gemini-2.5-flash","gemini-2.5-pro"],
                          captions=["빠름","고품질"], index=0,
                          label_visibility="collapsed", key="tag_model")
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 6. TTS 오디오 모델 (자동저장) ────
    st.markdown("<div class='sb-box'><div class='sb-title'>🔊 TTS 오디오 모델</div>",
                unsafe_allow_html=True)
    tts_model = st.radio("", ["gemini-2.5-flash-preview-tts","gemini-2.5-pro-preview-tts"],
                          captions=["빠름·저비용","고품질"], index=0,
                          label_visibility="collapsed", key="tts_model")
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 7. 제목 후 무음 ──────────────────
    st.markdown("<div class='sb-box'><div class='sb-title'>⏸️ 제목 후 무음</div>",
                unsafe_allow_html=True)
    title_pause = st.slider("", 0.5, 3.0, 1.5, 0.5,
                             format="%.1f초", label_visibility="collapsed", key="title_pause")
    st.markdown("</div>", unsafe_allow_html=True)


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

manuscript = st.text_area("", height=250,
    placeholder="여기에 원고를 붙여넣으세요...",
    label_visibility="collapsed", key="manuscript")

char_count = len(manuscript) if manuscript else 0
st.markdown(
    f"<p style='font-size:16px;font-weight:600;color:#7c3aed;margin:4px 0'>글자 수: {char_count:,}자</p>",
    unsafe_allow_html=True
)

col_q1, col_q2 = st.columns(2)
with col_q1:
    has_text = bool(manuscript and manuscript.strip())
    btn_label = "🔍 품질 검사 시작" if has_text else "✏️ 원고를 먼저 입력하세요"
    if st.button(btn_label, type="primary" if has_text else "secondary",
                 disabled=not (api_key and has_text), use_container_width=True):
        with st.spinner("Gemini가 원고 분석 중... (30초~1분)"):
            try:
                result = analyze_manuscript(api_key, manuscript, tag_model)
                st.session_state['analysis_result'] = result
                st.session_state['analysis_text'] = manuscript
                st.session_state['accepted_fixes'] = {}
                st.session_state.pop('manuscript_checked', None)
            except Exception as e:
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
        st.success("✅ 문제없음! 자동으로 다음 단계로 이동합니다.")
        st.session_state['manuscript_checked'] = st.session_state['analysis_text']
        st.rerun()
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
                        f"border-radius:8px;padding:8px;min-height:70px;font-size:13px'>"
                        f"<b style='color:#2d3748'>원본</b><br>"
                        f"<span style='color:#c53030'>{orig}</span></div>",
                        unsafe_allow_html=True
                    )
                    if st.button("👆 원본 선택", key=f"sel_o_{i}", use_container_width=True):
                        accepted[i] = {'type':'original','text':orig,'original':orig}
                        st.session_state['accepted_fixes'] = accepted
                        st.rerun()

                # 제안 카드
                with cs:
                    is_sel = sel_type == "suggestion"
                    st.markdown(
                        f"<div style='background:{'#f0fff4' if is_sel else '#f9fff9'};"
                        f"border:{'2px solid #276749' if is_sel else '1px solid #9ae6b4'};"
                        f"border-radius:8px;padding:8px;min-height:70px;font-size:13px'>"
                        f"<b style='color:#2d3748'>제안</b><br>"
                        f"<span style='color:#276749'>{sugg}</span></div>",
                        unsafe_allow_html=True
                    )
                    if st.button("✅ 제안 선택", key=f"sel_s_{i}", use_container_width=True):
                        accepted[i] = {'type':'suggestion','text':sugg,'original':orig}
                        st.session_state['accepted_fixes'] = accepted
                        st.rerun()

                # 직접 수정 카드 (버튼 없이 입력 후 자동저장)
                with cc:
                    is_sel   = sel_type == "custom"
                    cust_val = cur.get('text','') if is_sel else ''
                    st.markdown(
                        f"<div style='background:{'#fffbeb' if is_sel else '#fff'};"
                        f"border:{'2px solid #d97706' if is_sel else '1px solid #ddd'};"
                        f"border-radius:8px;padding:8px 8px 4px;font-size:13px'>"
                        f"<b style='color:#2d3748'>✏️ 직접 수정</b>"
                        f"{'<br><span style="color:#d97706;font-size:11px">저장됨</span>' if is_sel else ''}"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    st.text_input("", value=cust_val,
                                  placeholder="입력하면 자동저장...",
                                  label_visibility="collapsed",
                                  key=f"custom_inp_{i}",
                                  on_change=make_custom_cb(i, orig))

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

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("⬇️ 원고 저장",
            data=checked.encode("utf-8"),
            file_name=f"{project_name}_{chapter_name}_원고.txt",
            mime="text/plain", use_container_width=True)
    with c2:
        if st.button("🔄 태그 변환 시작", type="primary",
                     disabled=not api_key, use_container_width=True):
            with st.spinner("태그 변환 중... (30초~1분)"):
                try:
                    tags = PRESETS.get(selected_preset, PRESETS["일반소설"]).get("tags","")
                    tagged = convert_tags(api_key, checked, tag_model, speakers, tags)
                    st.session_state['tagged_script'] = normalize_tags(tagged)
                    st.session_state.pop('audio_data', None)
                    st.rerun()
                except Exception as e:
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
    total_chunks = sum(len(chunk_segment(s['lines'])) for s in segs)
    saved_calls  = sum(len(chunk_segment(s['lines'])) for s in segs_raw) - total_chunks
    st.caption(
        f"세그먼트 {len(segs_raw)}개 → 병합 {len(segs)}개  |  "
        f"청크 {total_chunks}개  |  API 절감 {saved_calls}회  |  {tts_model}"
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
        client     = genai.Client(api_key=api_key)
        progress   = st.progress(0)
        status     = st.empty()
        pcm_list   = list(load_progress().get('pcm_list',[])) if resume_from > 0 else []
        error_flag = False
        done       = resume_from
        chunk_idx  = 0

        for seg in segs:
            voice = get_voice_for_speaker(seg['speaker'], speakers)
            chunks = chunk_segment(seg['lines'])
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
                    pcm    = call_tts_single(client, script, voice, tts_model)
                    pcm_list.append(pcm)
                    done += 1
                    chunk_idx += 1
                    save_progress(list(pcm_list), done, chapter_name)
                except Exception as e:
                    st.error(f"❌ [{seg['speaker']}] {e}")
                    st.info(f"💾 {done}청크까지 저장됨. [▶️ 이어서 생성]으로 재시작하세요.")
                    error_flag = True
                    break
            if seg['is_title'] and not error_flag:
                pcm_list.append(generate_silence(title_pause))
            if error_flag:
                break

        if not error_flag and pcm_list:
            status.markdown("🔗 합치는 중...")
            wav = merge_to_wav(pcm_list)
            st.session_state['audio_data'] = wav
            clear_progress()
            progress.progress(1.0)
            status.markdown("🎧 완료!")

    if 'audio_data' in st.session_state:
        wav  = st.session_state['audio_data']
        mb   = len(wav) / 1024 / 1024
        fname = f"{project_name}_{chapter_name}.wav"
        st.success(f"✅ 완료 — {mb:.1f} MB")
        st.audio(wav, format="audio/wav")
        st.download_button(f"⬇️ {fname} 저장",
            data=wav, file_name=fname,
            mime="audio/wav", use_container_width=True)
