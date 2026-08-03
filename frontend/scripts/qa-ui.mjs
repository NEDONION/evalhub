import { chromium } from "playwright";

const baseUrl = process.env.EVALHUB_QA_URL || "http://127.0.0.1:8001";
const chromePath = process.env.EVALHUB_CHROME_PATH;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const browser = await chromium.launch({
  headless: true,
  ...(chromePath ? { executablePath: chromePath } : { channel: "chrome" }),
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});
const page = await context.newPage();
const consoleErrors = [];

page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});

try {
  const response = await page.goto(baseUrl, { waitUntil: "networkidle" });
  assert(response?.ok(), `页面加载失败：${response?.status() ?? "无响应"}`);
  await page.getByRole("heading", { level: 1, name: "工作台概览" }).waitFor();

  assert((await page.locator("aside").count()) === 1, "页面缺少工作区侧边栏");
  assert(
    (await page.getByRole("button", { name: "打开概览页面" }).getAttribute("aria-current")) === "page",
    "概览目录没有活动状态",
  );
  assert(
    (await page.locator('a:not([href]), a[href=""], a[href="#"]').count()) === 0,
    "页面包含无有效入口的链接",
  );

  const desktopLayout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    bodyBackground: getComputedStyle(document.body).backgroundColor,
  }));
  assert(
    desktopLayout.scrollWidth <= desktopLayout.viewportWidth,
    `桌面端横向溢出：${desktopLayout.scrollWidth} > ${desktopLayout.viewportWidth}`,
  );
  assert(desktopLayout.bodyBackground === "rgb(247, 249, 252)", "页面没有使用约定的蓝白底色");
  await page.screenshot({ path: "/tmp/evalhub-desktop.png", fullPage: true });

  await page.getByRole("button", { name: "打开资产管理页面" }).click();
  await page.getByRole("heading", { name: "数据集资产" }).waitFor();
  await page.getByRole("button", { name: "打开发起评测页面" }).click();
  await page.getByRole("heading", { level: 1, name: "发起评测" }).waitFor();
  await page.getByLabel("数据集", { exact: true }).selectOption("mmlu");
  await page.getByLabel("MMLU 学科").waitFor();
  await page.getByLabel("数据集", { exact: true }).selectOption("gsm8k");
  assert((await page.getByLabel("MMLU 学科").count()) === 0, "GSM8K 下仍显示 MMLU 学科字段");

  await page.getByText("自定义", { exact: true }).click();
  await page.getByLabel("自定义样本数量").fill("0");
  await page.getByRole("button", { name: "发起评测", exact: true }).click();
  await page.getByText("样本数量必须是大于 0 的整数", { exact: true }).waitFor();

  await page.getByLabel("模型适配器").selectOption("oracle");
  await page.getByText("快速试跑", { exact: true }).click();
  await page.getByRole("button", { name: "发起评测", exact: true }).click();
  const resultPanel = page.getByRole("region", { name: "评测结果" });
  await resultPanel.getByTitle("已完成").waitFor({ timeout: 30_000 });
  await resultPanel.getByText("5 / 5", { exact: true }).waitFor();
  await resultPanel.getByText("1.0000", { exact: true }).waitFor();

  const details = resultPanel.getByText("原始 JSON", { exact: true });
  await details.click();
  await resultPanel.getByText('"adapter": "oracle"', { exact: false }).waitFor();
  await page.screenshot({ path: "/tmp/evalhub-result.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("heading", { level: 1, name: "工作台概览" }).waitFor();
  const mobileLayout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert(
    mobileLayout.scrollWidth <= mobileLayout.viewportWidth,
    `移动端横向溢出：${mobileLayout.scrollWidth} > ${mobileLayout.viewportWidth}`,
  );
  await page.screenshot({ path: "/tmp/evalhub-mobile.png", fullPage: true });

  assert(consoleErrors.length === 0, `浏览器控制台错误：${consoleErrors.join(" | ")}`);
  console.log(
    JSON.stringify(
      {
        ok: true,
        baseUrl,
        checks: [
          "蓝白工作台加载",
          "侧边栏目录与活动状态",
          "MMLU 条件字段",
          "自定义样本校验",
          "Oracle 快速评测 5/5",
          "原始 JSON 展开",
          "桌面与 390px 移动端无横向溢出",
          "控制台无错误",
        ],
        screenshots: [
          "/tmp/evalhub-desktop.png",
          "/tmp/evalhub-result.png",
          "/tmp/evalhub-mobile.png",
        ],
        desktopLayout,
        mobileLayout,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}
