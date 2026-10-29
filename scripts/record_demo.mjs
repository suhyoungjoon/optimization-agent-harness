// 시연 장면 녹화 (발표용 사전 녹화 백업, plan.md 13장 · 시연 동영상 지시서 `시연동영상_녹화_전달.md`).
//
// 서버를 띄운 뒤:
//   node scripts/record_demo.mjs [--url http://127.0.0.1:8000] [--out runs/recordings] [--scenes 1,2,3]
//                                [--seed 42] [--faults P1,P2,P3,P4] [--scope D01-10] [--from L0] [--to L3]
//                                [--trace-level L5] [--opinion "관할 밖 4km 넘으면 출동 불가"]
//                                [--width 1920] [--height 1080] [--take <이름>]
//                                [--pace 1] [--headed] [--allow-live] [--stills]
//
// 장면 1~3: 시연 모드 서버(저장된 결과 재생)용 짧은 장면 (발표 백업).
// 장면 v1~v6: 시연 동영상 한 편 (실제 회사 AI 호출, --allow-live). 문제 → 규칙 대 AI → 자가 개선 흐름 → Supervisor →
//   승인 전 고객 기준 → 마무리. 장면마다 webm 하나, AI를 기다린 구간은 scene-<id>.marks.json (후처리가 그 구간만 배속),
//   화면에서 읽은 핵심 수치·비용·시간은 takes.json에 테이크 단위로 쌓는다 (자막 문구 확정용).
// 시연 모드가 아니면(실제 LLM 호출·비용 발생) --allow-live 없이는 멈춘다. 키는 화면·로그·파일에 남기지 않는다
// (녹화는 앱 화면만, 터미널·설정 화면은 찍지 않는다).
// playwright는 web/의 devDependency를 쓴다 (cd web && npm ci). 브라우저가 없으면 npx playwright install chromium.

import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = createRequire(join(ROOT, "web", "package.json"))("playwright");

const DEFAULTS = {
  url: "http://127.0.0.1:8000", out: join(ROOT, "runs", "recordings"), scenes: "1,2,3", seed: "42",
  faults: "P1,P2,P3,P4", scope: "D01-10", from: "L0", to: "L3", "trace-level": "L5", pace: "1", width: "1920", height: "1080",
  opinion: "관할 밖 4km 넘으면 출동 불가", take: "",
};

function parseArgs(argv) {
  const opts = { ...DEFAULTS, headed: false, allowLive: false, stills: false };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, "");
    if (key === "headed") opts.headed = true;
    else if (key === "allow-live") opts.allowLive = true;
    else if (key === "stills") opts.stills = true;
    else if (key in DEFAULTS) opts[key] = argv[++i];
    else throw new Error(`알 수 없는 옵션: ${argv[i]}`);
  }
  if (!opts.take) opts.take = new Date().toISOString().replace(/[-:]/g, "").slice(0, 15);
  return opts;
}

const opts = parseArgs(process.argv.slice(2));
const pause = (page, ms) => page.waitForTimeout(ms * Number(opts.pace));
const TIMEOUT = 120_000;
const AI_TIMEOUT = 20 * 60_000;      // 실제 AI 단계 하나 (분석 gpt-5 관점 3개 등)

let still = { scene: "", n: 0 };
let scene = { id: "", started: 0, marks: [], facts: {} };   // 지금 녹화 중인 장면: AI 대기 구간, 화면에서 읽은 수치

async function saveStill(page) {
  if (!opts.stills || !still.scene) return;
  still.n += 1;
  await page.screenshot({ path: join(opts.out, `scene-${still.scene}-${String(still.n).padStart(2, "0")}.png`) });
}

// 화면 하단 자막 (녹화에만 보인다). 1080p에서 읽히게 글자를 키운다
async function caption(page, text) {
  if (await page.locator("#rec-caption").count()) await saveStill(page);
  const size = Math.round(17 * Number(opts.height) / 900);
  await page.evaluate(([t, px]) => {
    let el = document.getElementById("rec-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "rec-caption";
      el.style.cssText = "position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:9999;"
        + "max-width:82%;padding:12px 22px;border-radius:10px;background:rgba(20,20,19,.9);color:#fff;"
        + `font:600 ${px}px/1.45 system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.25);pointer-events:none`;
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, [text, size]);
}

// AI를 기다리는 구간 표시: 시작·끝(장면 시작부터 초)을 남긴다. 후처리가 이 구간만 배속한다
async function waiting(label, fn) {
  const start = (Date.now() - scene.started) / 1000;
  try {
    return await fn();
  } finally {
    scene.marks.push({ label, start: Number(start.toFixed(2)), end: Number(((Date.now() - scene.started) / 1000).toFixed(2)) });
  }
}

const fact = (key, value) => { scene.facts[key] = value; };
const text = async (loc) => ((await loc.count()) ? (await loc.first().innerText()).replace(/\s+/g, " ").trim() : null);

async function openApp(page) {
  await page.goto(opts.url);
  await page.getByRole("tab", { name: "비교" }).waitFor({ timeout: TIMEOUT });
  // 시연 배너는 서버 설정(/harness/levels)을 받은 뒤에 그려진다. 같은 응답으로 그려지는 레벨 버튼을 기다린 뒤 확인한다
  await page.getByRole("radiogroup", { name: "하네스 레벨" }).waitFor({ timeout: TIMEOUT });
  const demo = await page.locator(".demo-banner").count();
  if (!demo && !opts.allowLive) {
    throw new Error("시연 모드 서버가 아님: 실제 LLM을 호출해 비용이 든다. 의도했다면 --allow-live");
  }
  return !demo;
}

// 비교 탭 (레이아웃 B): 왼쪽 칸에 데이터 번호·심어둔 문제·처리할 건수, 오른쪽 위에 데이터 생성
async function generate(page, scope = opts.scope) {
  const side = page.locator(".side-slot");
  await side.locator("label:has-text('데이터 번호') input").fill(opts.seed);
  const wanted = new Set(opts.faults.split(",").filter(Boolean));
  for (const box of await side.locator("fieldset label").all()) {
    const id = (await box.innerText()).trim().split(/\s+/)[0];
    await box.locator("input").setChecked(wanted.has(id));
  }
  await page.getByRole("button", { name: "데이터 생성" }).click();
  const select = side.locator("label:has-text('처리할 건수') select");
  await select.waitFor({ timeout: TIMEOUT });
  await select.selectOption(scope);
}

async function runAi(page, level) {
  // 레벨 버튼 이름은 "L3 +자동 검사"처럼 짧은 설명이 붙는다
  await page.getByRole("radiogroup", { name: "하네스 레벨" }).getByRole("radio", { name: new RegExp(`^${level}( |$)`) }).click();
  await page.getByRole("button", { name: /AI 방식 실행/ }).click();
  const error = page.locator(".compare .status-line .critical-text");
  const done = page.locator(".progress-list");
  await page.waitForTimeout(500);
  if (await error.count()) throw new Error(`${level} 실행 실패: ${await error.innerText()}`);
  await waiting(`AI 배정 ${level}`, () => done.waitFor({ state: "detached", timeout: AI_TIMEOUT }));
  await page.getByText("AI 방식", { exact: true }).locator("xpath=ancestor::article").locator("svg").first()
    .waitFor({ timeout: TIMEOUT });
}

// 비교표 한 줄 → {열 이름: 값}
async function compareRows(page) {
  return page.locator(".compare-table table").first().evaluate((t) => {
    const head = [...t.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    return [...t.querySelectorAll("tbody tr")].map((tr) =>
      Object.fromEntries([...tr.querySelectorAll("th,td")].map((c, i) => [head[i] ?? i, c.textContent.trim()])));
  });
}

// --- LangGraph agents 탭 ------------------------------------------------------
async function agentsSetup(page, { live, mode, perspectives }) {
  await page.getByRole("tab", { name: "LangGraph agents" }).click();
  const side = page.locator(".side-slot");
  await side.locator("label:has-text('데이터 번호') input").waitFor({ timeout: TIMEOUT });
  await side.locator("label:has-text('데이터 번호') input").fill(opts.seed);
  const wanted = new Set(opts.faults.split(",").filter(Boolean));
  for (const box of await side.locator("fieldset label").all()) {
    const id = (await box.innerText()).trim().split(/\s+/)[0];
    await box.locator("input").setChecked(wanted.has(id));
  }
  await side.locator("label:has-text('처리할 건수') input").fill("10");
  await side.locator("label:has-text('하네스 레벨') select").selectOption(opts.to);
  const ai = side.locator("label:has-text('AI') select").first();
  if (live && await ai.isEnabled()) await ai.selectOption("claude");
  await side.locator("label:has-text('관점별 분석') input").setChecked(perspectives);
  await side.locator("label:has-text('저장된 제약 불러오기') input").setChecked(false);   // 테이크마다 같은 출발점
  await side.locator("label:has-text('Supervisor 지휘') input").setChecked(mode === "supervisor");
  await side.locator("label:has-text('종료 판정') select").selectOption("fixed");
  await side.locator("label:has-text('속도') select").selectOption("0.1");
  await page.getByRole("button", { name: /^(에이전트 준비|처음부터 다시)$/ }).click();
  await page.getByRole("button", { name: /다음 실행/ }).waitFor({ timeout: TIMEOUT });
}

// 단계 진행: 사람 단계는 review(page, n)로 넘기고, stop이 참이면 그 자리에서 멈춘다
async function agentsDrive(page, { review, stopAtReview = false, maxSteps = 60 }) {
  const bar = page.locator(".wf-bar");
  let reviews = 0;
  for (let i = 0; i < maxSteps; i++) {
    const status = (await bar.innerText()).trim();
    if (status.startsWith("✓ 끝")) return reviews;
    if (await page.getByRole("button", { name: "확정", exact: true }).count()) {      // 제약 확인 (사람)
      await pause(page, 2500);
      await page.getByRole("button", { name: "확정", exact: true }).click();
    } else if (await page.getByRole("button", { name: "승인", exact: true }).count()) {   // 사람 검토
      if (stopAtReview) return reviews;
      await review(page, reviews++);
    } else {
      const next = page.getByRole("button", { name: /다음 실행/ });
      const label = status.replace(/^다음 단계:\s*/, "").split(/\n/)[0];
      const before = await page.locator(".agt-row:not(.agt-running)").count();
      await next.click();
      // 단계 하나가 끝나면 타임라인에 줄이 하나 늘고 "실행 중"이 사라진다
      await waiting(label, () => page.waitForFunction((n) => document.querySelectorAll(".agt-row:not(.agt-running)").length > n
        && !/실행 중/.test(document.querySelector(".wf-bar")?.textContent ?? ""), before, { timeout: AI_TIMEOUT }));
    }
    await page.waitForTimeout(600);
    const err = await text(page.locator(".wf-bar .critical-text, .run-bar .critical-text"));
    if (err) throw new Error(`단계 실패: ${err}`);
  }
  throw new Error("단계가 너무 많다 (흐름이 끝나지 않음)");
}

// 타임라인 줄 → [{time, kind, name, text, meta}]
async function timelineRows(page) {
  return page.locator(".agt-row").evaluateAll((rows) => rows.map((r) => ({
    kind: r.querySelector(".agt-kind")?.textContent.trim(), name: r.querySelector(".agt-name")?.textContent.trim(),
    text: r.querySelector(".agt-text")?.textContent.trim(), meta: r.querySelector(".agt-meta")?.textContent.trim() })));
}

const costOf = (rows) => {
  const vals = rows.map((r) => /\$([\d.]+)/.exec(r.meta ?? "")?.[1]).filter(Boolean).map(Number);
  return vals.length ? Number(vals.reduce((a, b) => a + b, 0).toFixed(4)) : null;
};

const scenes = {
  // 장면 1: 하네스 토글. 같은 지시서를 L0와 L3로 실행해 지도·비교표의 위반이 사라지는 모습
  async 1(page) {
    await openApp(page);
    await page.getByRole("tab", { name: "비교" }).click();
    await caption(page, `장면 1 · 하네스 레벨 ${opts.from} → ${opts.to}`);
    await generate(page);
    await pause(page, 1500);
    await caption(page, `${opts.from}: 업무 규칙 문서·조회 기능·자동 검사 없이 AI 방식 실행`);
    await runAi(page, opts.from);
    await page.locator(".compare .split").scrollIntoViewIfNeeded();
    await pause(page, 4000);
    await caption(page, `${opts.to}: 업무 규칙 문서 + 조회 기능 + 자동 검사 (규칙을 어기면 이유를 돌려주고 다시 시도)`);
    await runAi(page, opts.to);
    await pause(page, 4000);
    await caption(page, "레벨별 비교: 규칙 위반과 배정 성공률, 건당 비용");
    await page.locator(".compare-table").scrollIntoViewIfNeeded();
    await pause(page, 5000);
  },

  // 장면 2: 결정 과정(트레이스). 다시 시도한 지시서 하나의 AI 판단 과정과 규칙 방식 판단 비교
  // (단계 기록은 트레이스를 켠 레벨에만 있다: harness_levels.yaml의 trace)
  async 2(page) {
    await openApp(page);
    await caption(page, "장면 2 · 지시서 하나를 AI가 결정한 과정");
    await generate(page);
    await runAi(page, opts["trace-level"]);
    await page.getByRole("tab", { name: "결정 과정" }).click();
    const filters = page.getByRole("radiogroup", { name: "항목 필터" });
    await filters.waitFor({ timeout: TIMEOUT });
    const retried = filters.getByRole("radio", { name: /다시 시도한 건/ });
    if (!(await retried.innerText()).includes("(0)")) await retried.click();
    await pause(page, 1000);
    await page.getByRole("list", { name: "항목" }).getByRole("button").first().click();
    await page.locator(".trace .verdict").waitFor({ timeout: TIMEOUT });
    await caption(page, "결론 카드: AI 결정이 규칙 방식과 같은지, 몇 번 다시 시도했는지. 아래는 단계 흐름 한 줄");
    await pause(page, 3500);
    await page.locator(".trace details.more > summary").first().click();
    await caption(page, "상세보기: AI 응답 → 조회 → 자동 검사 → 다시 시도 단계별 기록");
    await pause(page, 3000);
    await page.locator(".trace").evaluate((el) => el.scrollIntoView({ block: "start" }));
    await page.mouse.wheel(0, 500);
    await pause(page, 3000);
    await caption(page, "같은 지시서의 규칙 방식 판단과 나란히 비교");
    await page.getByText("규칙 방식", { exact: true }).first().scrollIntoViewIfNeeded();
    await pause(page, 4000);
  },

  // 장면 3: AI 분석이 심어둔 문제를 찾고, 개선 제안을 미리 돌려봐 배정 성공률이 오르는 모습
  async 3(page) {
    await openApp(page);
    await caption(page, "장면 3 · AI 분석과 개선 제안");
    await generate(page);
    await page.getByRole("tab", { name: "문제 찾기" }).click();
    await page.getByRole("button", { name: "규칙 방식 전체 실행" }).click();
    await page.locator(".side-slot select option").filter({ hasText: "전체" }).first().waitFor({ state: "attached", timeout: TIMEOUT });
    await page.getByRole("button", { name: "AI 분석 실행" }).click();
    await waiting("AI 분석", () => page.locator(".analysis .verdict").waitFor({ timeout: AI_TIMEOUT }));
    await caption(page, "결론 한 줄: 심어둔 문제를 몇 개 찾았는지 정답과 대조. 찾은 문제는 제목과 조건만 먼저 보인다");
    await pause(page, 3500);
    const first = page.locator(".finding-title").first();
    await first.click();
    await caption(page, "찾은 문제를 누르면 해당 조건의 지시서가 지도에 강조된다");
    await pause(page, 4000);

    await page.getByRole("tab", { name: "개선 제안" }).click();
    await page.getByRole("button", { name: "개선 제안 만들기" }).click();
    await waiting("개선 제안", () => page.locator(".proposal").first().waitFor({ timeout: AI_TIMEOUT }));
    const proposal = page.locator(".proposal-proposed").filter({ hasText: "규칙 설정값" }).first();
    if (!(await proposal.count())) {
      throw new Error("결정 전 규칙 설정값 개선 제안이 없음. 이미 승인했다면 시연 서버를 다시 시작하세요 (임시 사본 초기화)");
    }
    await caption(page, "개선 제안: 규칙 설정값 또는 업무 규칙 문서 수정 (바꿀 수 있는 범위 검사 포함)");
    await pause(page, 3000);
    await proposal.getByRole("button", { name: "미리 돌려보기", exact: true }).click();
    const simulated = page.locator(".proposal-simulated").first();
    await simulated.locator("p.gist:not(.muted)").waitFor({ timeout: TIMEOUT });
    await simulated.scrollIntoViewIfNeeded();
    await caption(page, "미리 돌려보기: 요약 한 줄로 핵심 지표 변화와 나빠진 지표를 먼저 보여준다");
    await pause(page, 3500);
    await simulated.locator("details.more > summary").first().click();
    await simulated.locator(".sim-table").waitFor({ timeout: TIMEOUT });
    await caption(page, "상세보기를 펼치면 근거, 바꿀 내용, 전·후 전체 지표");
    await pause(page, 4500);
    await simulated.getByRole("button", { name: "승인", exact: true }).click();
    await page.locator(".history").waitFor({ timeout: TIMEOUT });
    await page.locator(".history").scrollIntoViewIfNeeded();
    await caption(page, "승인하면 규칙 설정값 버전이 오르고 반영 이력에 남는다 (git 커밋은 사람이)");
    await pause(page, 5000);
  },

  // --- 시연 동영상 (v1~v6) ------------------------------------------------------
  // v1 문제: 규칙 엔진이 전체를 배정한 결과
  async v1(page) {
    await openApp(page);
    await page.getByRole("tab", { name: "규칙·데이터" }).click();
    await caption(page, "출동 배정 업무: 지점 3곳 · 작업자 30명 · 지시서 하루 150건 × 10일. 배정은 규칙 엔진이 한다");
    await pause(page, 5000);
    await page.getByRole("tab", { name: "비교" }).click();
    await generate(page, "all");
    await page.getByRole("button", { name: /규칙 방식 실행/ }).click();
    const rule = page.getByText("규칙 방식", { exact: true }).locator("xpath=ancestor::article");
    await rule.locator("svg").first().waitFor({ timeout: TIMEOUT });
    const rate = await rule.locator(".tile").filter({ hasText: "배정 성공률" }).locator(".tile-value").first().innerText();
    fact("rule_assignment_rate", rate.trim());
    await caption(page, `규칙 방식 전체 1500건: 배정 성공 ${rate.trim()}. 왜 실패했는지는 사람이 매번 찾아야 한다`);
    await pause(page, 7000);
  },

  // v2 규칙 대 AI 배정: 같은 10건을 L0 → L3
  async v2(page) {
    await openApp(page);
    await page.getByRole("tab", { name: "비교" }).click();
    await generate(page);
    await page.getByRole("button", { name: /규칙 방식 실행/ }).click();
    await caption(page, `같은 10건을 AI로 배정한다. ${opts.from}: 도구·규칙 문서·검사 없는 AI`);
    await runAi(page, opts.from);
    await page.locator(".compare .split").scrollIntoViewIfNeeded();
    await pause(page, 4000);
    await caption(page, `${opts.to}: 규칙 문서 + 조회 도구 + 규칙 위반 검사(어기면 이유를 돌려주고 다시 시도)`);
    await runAi(page, opts.to);
    const rows = await compareRows(page);
    fact("compare", rows);
    const row = (k) => rows.find((r) => String(r["방식"] ?? "").includes(k)) ?? {};
    const l0 = row(opts.from), l3 = row(opts.to);
    await page.locator(".compare-table").scrollIntoViewIfNeeded();
    await caption(page, `${opts.from} 규칙 위반 ${l0["규칙 위반"] ?? "?"} → ${opts.to} 위반 ${l3["규칙 위반"] ?? "?"}, 건당 ${l3["건당 비용"] ?? "?"}. `
      + "규칙과 같은 결과에 비용·시간이 더 든다 → AI는 배정이 아니라 분석·개선에 쓴다");
    await pause(page, 8000);
  },

  // v3 자가 개선 흐름 (고정 흐름): 분석(관점 3개) → 개선 제안 → 미리 돌려보기 → 의견 → 제약 → 다시 제안 → 승인 → 반영·재평가 → 결과 보고
  async v3(page) {
    const live = await openApp(page);
    await agentsSetup(page, { live, mode: "fixed", perspectives: true });
    await caption(page, "LangGraph agents · 고정 흐름: AI는 판단하고 제안한다. 코드가 검사·계산하고, 사람이 승인한다");
    await page.locator(".ag-timeline").scrollIntoViewIfNeeded().catch(() => undefined);
    const reviews = await agentsDrive(page, {
      review: async (p, n) => {
        if (n === 0) {
          await caption(p, `사람 검토: 현장 의견 "${opts.opinion}" → 의견 정리 에이전트가 제약으로 정리한다`);
          await p.getByRole("textbox", { name: "메모 또는 의견" }).fill(opts.opinion);
          await pause(p, 2500);
          await p.locator("button", { hasText: "의견 보내기" }).click();
        } else {
          await caption(p, "제약을 지킨 새 제안을 사람이 승인한다 → 반영·재배정·재평가");
          await pause(p, 3000);
          await p.getByRole("button", { name: "승인", exact: true }).click();
        }
      },
    });
    fact("reviews", reviews);
    const rows = await timelineRows(page);
    fact("timeline", rows);
    fact("cost_usd", costOf(rows));
    const report = rows.filter((r) => r.name === "결과 보고").map((r) => r.text);
    fact("report", report);
    await page.locator(".loop-panel").scrollIntoViewIfNeeded().catch(() => undefined);
    await caption(page, `결과 보고 (이번 실행): ${report[0] ?? "결과 보고 확인"}`);
    await pause(page, 7000);
    await page.getByRole("button", { name: "기록 저장" }).click();
    await page.locator(".run-bar .good-text").waitFor({ timeout: TIMEOUT });
    fact("record", await text(page.locator(".run-bar .good-text")));
  },

  // v4 Supervisor: 스스로 분석·개선을 지휘하고 판단 보고서를 낸다 (사람 검토 앞에서 멈춤, 승인하지 않음)
  async v4(page) {
    const live = await openApp(page);
    await agentsSetup(page, { live, mode: "supervisor", perspectives: false });
    await caption(page, "Supervisor 지휘: 분석·개선 에이전트와 계산 도구를 도구로 부르며 다음 행동을 스스로 정한다");
    await agentsDrive(page, { review: async () => undefined, stopAtReview: true });
    const rows = await timelineRows(page);
    fact("timeline", rows);
    fact("cost_usd", costOf(rows));
    const sup = rows.find((r) => r.name === "Supervisor 지휘");
    const attempts = await page.locator(".agt-sub").evaluateAll((els) => els.map((e) => e.textContent.trim()));
    fact("attempts", attempts);
    const card = page.locator(".judgment-card");
    fact("judgment", await text(card));
    const asks = await card.locator("text=사람에게 묻는").count();
    await page.locator(".ag-timeline").scrollIntoViewIfNeeded();
    await caption(page, `이번 실행: ${sup?.text ?? "Supervisor 지휘"} · 시도 ${attempts.length}번`);
    await pause(page, 6000);
    await card.scrollIntoViewIfNeeded();
    await caption(page, asks ? "판단 보고서: 추천안·근거·확신도. 득실이 큰 결정은 직접 고르지 않고 사람에게 한도를 묻는다"
      : "판단 보고서: 추천안·근거·확신도·원인 가설·시도 기록. 반영은 사람이 승인한 뒤에만");
    await pause(page, 8000);
  },

  // v5 승인 전 고객 기준: 개선안 카드의 계획 vs 가상 실적 (규칙 엔진·실적 계산만, AI 호출 없음)
  async v5(page) {
    await openApp(page);
    await page.getByRole("tab", { name: "에이전트", exact: true }).click();
    await page.locator(".agent-pick", { hasText: "개선" }).first().click();
    const box = page.locator(".agent-history-box");
    await box.locator("summary").click();
    await box.locator(".agent-history button").first().click();
    await page.getByRole("button", { name: /개선 제안 탭에서 보기/ }).click({ timeout: TIMEOUT });
    await page.locator(".improvement .run-bar .status-line .muted").first().waitFor({ timeout: TIMEOUT });
    // 제시 기준에서 본안이 없으면(예: 승인으로 규칙이 바뀐 뒤) 접힌 참고 안을 펼친다
    const folded = page.locator(".improvement details > summary", { hasText: "참고 (접음)" });
    if (!(await page.locator(".proposal:visible").count()) && await folded.count()) await folded.first().click();
    const card = page.locator(".proposal:visible").filter({ hasText: "규칙 설정값" }).first();
    await card.waitFor({ timeout: TIMEOUT });
    await card.scrollIntoViewIfNeeded();
    await caption(page, "승인 전에: 개선안을 미리 돌려보고(규칙 엔진) 가상 고객 반응으로 계획 대비 실적을 본다");
    const sim = card.getByRole("button", { name: "미리 돌려보기", exact: true });
    if (await sim.count()) await waiting("미리 돌려보기", async () => { await sim.click(); await card.locator(".outcome-box table").waitFor({ timeout: TIMEOUT }); });
    const outcome = card.locator(".outcome-box");
    await outcome.locator("table").waitFor({ timeout: TIMEOUT });
    await outcome.scrollIntoViewIfNeeded();
    const rows = await outcome.locator("table tr").evaluateAll((trs) => trs.map((tr) => [...tr.children].map((c) => c.textContent.trim())));
    fact("outcome", rows);
    // 이번 테이크의 실제 수치로: 계획 배정·정시 배정과 실적(약속 준수·불만율)의 현재 → 이 안
    const row = (k) => rows.find((r) => r[0]?.includes(k));
    const change = ["계획 · 배정", "계획 · 정시 배정", "약속 준수", "불만율"].map(row).filter(Boolean)
      .map((r) => `${r[0].replace(/^(계획|실적) · /, "")} ${r[1]}→${r[2]}`).join(", ");
    await caption(page, change ? `이 안: ${change}. 배정이 늘어도 고객 기준이 나빠지는지 승인 전에 본다 (가상 실적은 사람만 참고, AI에는 숨김)`
      : "계획 대비 가상 실적: 사람만 참고하고 AI에는 숨긴다");
    await pause(page, 9000);
  },

  // v6 마무리: v3에서 저장한 기록을 다시 열어 결과 보고 (AI 호출 없음)
  async v6(page) {
    await openApp(page);
    await page.getByRole("tab", { name: "LangGraph agents" }).click();
    await page.getByRole("button", { name: "기록 열기" }).click();
    const pick = page.getByRole("combobox", { name: "저장한 실행 기록" });
    await pick.waitFor({ timeout: TIMEOUT });
    const value = await pick.locator("option").nth(1).getAttribute("value");
    if (!value) throw new Error("저장한 기록이 없음 (v3를 먼저 녹화)");
    await pick.selectOption(value);
    await page.getByRole("button", { name: "끝으로" }).click();
    await page.locator(".loop-panel").scrollIntoViewIfNeeded().catch(() => undefined);
    fact("record", value);
    await caption(page, "하네스가 AI 출력을 믿을 수 있는 결과로 바꾼다. 반영은 항상 사람 승인 뒤");
    await pause(page, 6000);
    await caption(page, "다음 단계: 실제 실적 데이터 연동");
    await pause(page, 5000);
  },
};

async function record(browser, id) {
  const dir = join(opts.out, `.tmp-${id}`);
  const size = { width: Number(opts.width), height: Number(opts.height) };
  scene = { id, started: Date.now(), marks: [], facts: {} };   // 영상은 컨텍스트를 만들 때 시작한다: 대기 구간 시각의 기준
  const context = await browser.newContext({ viewport: size, recordVideo: { dir, size } });
  const page = await context.newPage();
  still = { scene: id, n: 0 };
  let error = null;
  try {
    await scenes[id](page);
    await saveStill(page);
  } catch (e) {
    error = String(e?.message ?? e);
    await page.screenshot({ path: join(opts.out, `scene-${id}-error.png`) }).catch(() => undefined);
  } finally {
    await context.close();                    // 여기서 영상이 기록된다
  }
  const target = join(opts.out, `scene-${id}.webm`);
  renameSync(await page.video().path(), target);
  rmSync(dir, { recursive: true, force: true });
  const seconds = Number(((Date.now() - scene.started) / 1000).toFixed(1));
  writeFileSync(join(opts.out, `scene-${id}.marks.json`), JSON.stringify({ scene: id, seconds, marks: scene.marks }, null, 1));
  console.log(`장면 ${id}: ${target} (${seconds}초, AI 대기 구간 ${scene.marks.length}개)${error ? ` ✕ ${error}` : ""}`);
  return { id, seconds, waits: scene.marks.length, wait_seconds: Number(scene.marks.reduce((a, m) => a + m.end - m.start, 0).toFixed(1)),
    facts: scene.facts, error };
}

// 테이크 기록: 장면마다 화면에서 읽은 핵심 수치·비용·시간 (자막 문구 확정용). 같은 테이크 이름이면 장면을 덮어쓴다
function saveTake(results) {
  const path = join(opts.out, "takes.json");
  const takes = existsSync(path) ? JSON.parse(readFileSync(path, "utf-8")) : [];
  let take = takes.find((t) => t.take === opts.take);
  if (!take) takes.push(take = { take: opts.take, url: opts.url, live: opts.allowLive, started: new Date().toISOString(), scenes: {} });
  for (const r of results) take.scenes[r.id] = r;
  const costs = Object.values(take.scenes).map((s) => s.facts?.cost_usd).filter((c) => typeof c === "number");
  take.cost_usd = costs.length ? Number(costs.reduce((a, b) => a + b, 0).toFixed(4)) : null;
  writeFileSync(path, JSON.stringify(takes, null, 1));
  console.log(`테이크 ${opts.take}: ${path}${take.cost_usd != null ? ` · AI 비용(타임라인 합, 추정) $${take.cost_usd}` : ""}`);
}

// 시연 장면은 상태를 바꾸지 않는 장면부터 녹화한다: v3이 승인(규칙 버전↑)·제약 저장을 하므로 v5(개선안 카드)를 먼저,
// v6(v3의 기록 열기)은 v3 뒤에. 영상은 후처리가 v1~v6 순서로 잇는다
const SAFE_ORDER = ["v1", "v2", "v5", "v4", "v3", "v6"];
const asked = opts.scenes.split(",").filter(Boolean);
const ids = [...asked.filter((id) => !SAFE_ORDER.includes(id)), ...SAFE_ORDER.filter((id) => asked.includes(id))];
for (const id of ids) if (!scenes[id]) throw new Error(`없는 장면: ${id} (1, 2, 3, v1~v6)`);
mkdirSync(opts.out, { recursive: true });
const browser = await chromium.launch({ headless: !opts.headed });
const results = [];
try {
  for (const id of ids) results.push(await record(browser, id));
} finally {
  await browser.close();
  if (ids.some((id) => id.startsWith("v"))) saveTake(results);
}
if (results.some((r) => r.error)) process.exitCode = 1;
