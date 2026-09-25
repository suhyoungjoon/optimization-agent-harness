// 시연 장면 녹화 (발표용 사전 녹화 백업, plan.md 13장).
//
// 시연 모드 서버(python -m api.main --demo <번들>)를 띄운 뒤:
//   node scripts/record_demo.mjs [--url http://127.0.0.1:8000] [--out runs/recordings] [--scenes 1,2,3]
//                                [--seed 42] [--faults P1,P2,P3,P4] [--scope D01-10] [--from L0] [--to L3]
//                                [--trace-level L5]
//                                [--pace 1] [--headed] [--allow-live] [--stills]
//
// 장면마다 webm 파일 하나를 만든다 (--stills면 자막이 바뀌기 직전 화면도 png로 남긴다). 시연 모드가 아니면(실제 LLM 호출·비용 발생) --allow-live 없이는 멈춘다.
// playwright는 web/의 devDependency를 쓴다 (cd web && npm ci). 브라우저가 없으면 npx playwright install chromium.

import { mkdirSync, renameSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = createRequire(join(ROOT, "web", "package.json"))("playwright");

const DEFAULTS = {
  url: "http://127.0.0.1:8000", out: join(ROOT, "runs", "recordings"), scenes: "1,2,3", seed: "42",
  faults: "P1,P2,P3,P4", scope: "D01-10", from: "L0", to: "L3", "trace-level": "L5", pace: "1", width: "1440", height: "900",
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
  return opts;
}

const opts = parseArgs(process.argv.slice(2));
const pause = (page, ms) => page.waitForTimeout(ms * Number(opts.pace));
const TIMEOUT = 120_000;

let still = { scene: "", n: 0 };

async function saveStill(page) {
  if (!opts.stills || !still.scene) return;
  still.n += 1;
  await page.screenshot({ path: join(opts.out, `scene-${still.scene}-${String(still.n).padStart(2, "0")}.png`) });
}

// 화면 하단 자막 (녹화에만 보인다)
async function caption(page, text) {
  if (await page.locator("#rec-caption").count()) await saveStill(page);
  await page.evaluate((t) => {
    let el = document.getElementById("rec-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "rec-caption";
      el.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;"
        + "max-width:80%;padding:10px 18px;border-radius:8px;background:rgba(20,20,19,.88);color:#fff;"
        + "font:600 17px/1.4 system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.25);pointer-events:none";
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}

async function openApp(page) {
  await page.goto(opts.url);
  await page.getByRole("tab", { name: "비교" }).waitFor({ timeout: TIMEOUT });
  const demo = await page.locator(".demo-banner").count();
  if (!demo && !opts.allowLive) {
    throw new Error("시연 모드 서버가 아님: 실제 LLM을 호출해 비용이 든다. 의도했다면 --allow-live");
  }
}

async function generate(page) {
  await page.locator("label:has-text('seed') input").fill(opts.seed);
  const wanted = new Set(opts.faults.split(",").filter(Boolean));
  for (const box of await page.locator("fieldset label").all()) {
    const id = (await box.innerText()).trim().split(/\s+/)[0];
    await box.locator("input").setChecked(wanted.has(id));
  }
  await page.getByRole("button", { name: "데이터 생성" }).click();
  const scope = page.locator("label:has-text('실행 범위') select");
  await scope.waitFor({ timeout: TIMEOUT });
  await scope.selectOption(opts.scope);
}

async function runAi(page, level) {
  await page.getByRole("radiogroup", { name: "하네스 레벨" }).getByRole("radio", { name: level, exact: true }).click();
  await page.getByRole("button", { name: /AI agent 실행/ }).click();
  const error = page.locator(".compare .status-line .critical-text");
  const done = page.locator(".progress-list");
  await page.waitForTimeout(500);
  if (await error.count()) throw new Error(`${level} 실행 실패: ${await error.innerText()}`);
  await done.waitFor({ state: "detached", timeout: TIMEOUT });
  await page.getByText("AI agent", { exact: true }).locator("xpath=ancestor::article").locator("svg").first()
    .waitFor({ timeout: TIMEOUT });
}

const scenes = {
  // 장면 1: 하네스 토글. 같은 지시서를 L0와 L3로 실행해 지도·비교표의 위반이 사라지는 모습
  async 1(page) {
    await openApp(page);
    await caption(page, `장면 1 · 하네스 레벨 ${opts.from} → ${opts.to}`);
    await generate(page);
    await pause(page, 1500);
    await caption(page, `${opts.from}: 명세·도구·검증 없이 AI agent 실행`);
    await runAi(page, opts.from);
    await page.locator(".compare .split").scrollIntoViewIfNeeded();
    await pause(page, 4000);
    await caption(page, `${opts.to}: 명세 + 도구 + 검증 루프 (위반이면 사유를 돌려주고 재시도)`);
    await runAi(page, opts.to);
    await pause(page, 4000);
    await caption(page, "레벨별 비교: 필수조건 위반과 할당성공률, 건당 비용");
    await page.locator(".compare-table").scrollIntoViewIfNeeded();
    await pause(page, 5000);
  },

  // 장면 2: 트레이스. 재시도가 있었던 지시서 하나의 AI 판단 과정과 규칙 agent 판단 비교
  // (단계 기록은 트레이스를 켠 레벨에만 있다: harness_levels.yaml의 trace)
  async 2(page) {
    await openApp(page);
    await caption(page, "장면 2 · 지시서 하나의 AI agent 트레이스");
    await generate(page);
    await runAi(page, opts["trace-level"]);
    await page.getByRole("tab", { name: "트레이스" }).click();
    const filters = page.getByRole("radiogroup", { name: "항목 필터" });
    await filters.waitFor({ timeout: TIMEOUT });
    const retried = filters.getByRole("radio", { name: /재시도 있음/ });
    if (!(await retried.innerText()).includes("(0)")) await retried.click();
    await pause(page, 1000);
    await page.getByRole("list", { name: "항목" }).getByRole("button").first().click();
    await caption(page, "LLM 응답 → 도구 호출 → 검증 → 재시도: 단계별 기록");
    await pause(page, 3000);
    await page.locator(".trace").evaluate((el) => el.scrollIntoView({ block: "start" }));
    await page.mouse.wheel(0, 500);
    await pause(page, 3000);
    await caption(page, "같은 지시서의 규칙 agent 판단과 나란히 비교");
    await page.getByText("규칙 agent", { exact: true }).first().scrollIntoViewIfNeeded();
    await pause(page, 4000);
  },

  // 장면 3: 분석 agent가 심어둔 문제를 찾고, 개선안 시뮬레이션으로 할당성공률이 오르는 모습
  async 3(page) {
    await openApp(page);
    await caption(page, "장면 3 · 분석 agent와 개선 루프");
    await generate(page);
    await page.getByRole("tab", { name: "분석" }).click();
    await page.getByRole("button", { name: "규칙 agent 전체 실행" }).click();
    await page.locator(".analysis select option").filter({ hasText: "전체" }).first().waitFor({ state: "attached", timeout: TIMEOUT });
    await page.getByRole("button", { name: "분석 agent 실행" }).click();
    await page.locator(".analysis .tiles").waitFor({ timeout: TIMEOUT });
    await caption(page, "정답표(심어둔 패턴)와 매칭한 탐지율. 발견마다 근거 집계가 붙는다");
    await pause(page, 3500);
    const first = page.locator(".finding-title").first();
    await first.click();
    await caption(page, "발견을 누르면 해당 구간이 지도에 강조된다");
    await pause(page, 4000);

    await page.getByRole("tab", { name: "개선" }).click();
    await page.getByRole("button", { name: "개선안 생성" }).click();
    await page.locator(".proposal").first().waitFor({ timeout: TIMEOUT });
    const proposal = page.locator(".proposal-proposed").filter({ hasText: "규칙 파라미터" }).first();
    if (!(await proposal.count())) {
      throw new Error("결정 전 규칙 파라미터 개선안이 없음. 이미 승인했다면 시연 서버를 다시 시작하세요 (작업 복사본 초기화)");
    }
    await caption(page, "개선안: params.yaml 변경 또는 domain-spec.md 수정 (범위 검증 포함)");
    await pause(page, 3000);
    await proposal.getByRole("button", { name: "시뮬레이션" }).click();
    const simulated = page.locator(".proposal-simulated").first();
    await simulated.locator(".sim-table").waitFor({ timeout: TIMEOUT });
    await simulated.scrollIntoViewIfNeeded();
    await caption(page, "시뮬레이션: 개선 전·후 지표 비교");
    await pause(page, 4500);
    await simulated.getByRole("button", { name: "승인", exact: true }).click();
    await page.locator(".history").waitFor({ timeout: TIMEOUT });
    await page.locator(".history").scrollIntoViewIfNeeded();
    await caption(page, "승인하면 params 버전이 오르고 개선 이력에 남는다 (git 커밋은 사람이)");
    await pause(page, 5000);
  },
};

async function record(browser, id) {
  const dir = join(opts.out, `.tmp-${id}`);
  const size = { width: Number(opts.width), height: Number(opts.height) };
  const context = await browser.newContext({ viewport: size, recordVideo: { dir, size } });
  const page = await context.newPage();
  const started = Date.now();
  still = { scene: id, n: 0 };
  try {
    await scenes[id](page);
    await saveStill(page);
  } finally {
    await context.close();                    // 여기서 영상이 기록된다
  }
  const target = join(opts.out, `scene-${id}.webm`);
  renameSync(await page.video().path(), target);
  rmSync(dir, { recursive: true, force: true });
  console.log(`장면 ${id}: ${target} (${((Date.now() - started) / 1000).toFixed(1)}초)`);
}

const ids = opts.scenes.split(",").filter(Boolean);
for (const id of ids) if (!scenes[id]) throw new Error(`없는 장면: ${id} (1, 2, 3)`);
mkdirSync(opts.out, { recursive: true });
const browser = await chromium.launch({ headless: !opts.headed });
try {
  for (const id of ids) await record(browser, id);
} finally {
  await browser.close();
}
