/**
 * Browser half of the serial-job-session bundle.
 *
 * Registers one page in Settings (and the same page as the bundle's own
 * configuration in the Plugins page) that adjusts the plugin's runtime
 * parameters. It reads and writes the Host through the bundle's own JSON API and
 * never inspects another plugin's DOM or styles.
 *
 * The module is plain JavaScript: the Harness module loader calls this factory
 * lazily and supplies React through the shared module table.
 */

window.__ModuleLoader__.load({
  id: 'dsh-serial-job-session',
  factory(require) {
    const React = require('react')
    const h = React.createElement

    /** JSON API served by the Host half. */
    const API = {
      state: '/serial-job-session/api/state',
      settings: '/serial-job-session/api/settings',
      probe: '/serial-job-session/api/probe',
    }
    /** Locale namespace of this panel. */
    const LOCALE_NS = 'serialJobSession'

    const ZH = {
      sectionLabel: '串行任务',
      summary: '解释器、超时与额外 CLI 参数',
      title: '串行任务参数',
      intro:
        '这些参数作用于本机安装的 serial-job-session 插件：`sjs` 工具每次调用都会读取当前设置，保存后立即生效，不需要重启 Harness。',
      interpreter: 'Python 解释器',
      interpreterHint: '留空表示自动探测，只接受 Python 3.11+（2.x 会被拒绝）。优先顺序：环境变量 SJS_PYTHON → 本设置 → python3 / python / py。',
      interpreterPlaceholder: '自动（依次尝试 python / python3 / py）',
      timeout: '单次调用超时（毫秒）',
      timeoutHint: '范围 1000–600000。环境变量 SJS_TIMEOUT_MS 会覆盖本设置。',
      extraArgs: '额外 CLI 参数',
      extraArgsHint: '每行一个参数，追加到每次 sjs 调用的命令行；例如 --append-library。',
      save: '保存',
      saving: '保存中…',
      saved: '已保存',
      reset: '恢复默认',
      probe: '检测环境',
      probing: '检测中…',
      probeOk: '环境可用',
      probeFail: '环境不可用',
      info: '只读信息',
      repo: '仓库目录',
      cli: 'CLI 脚本',
      skill: 'Skill 名称',
      modelInvocation: '模型按需加载',
      settingsFile: '配置文件',
      version: '版本',
      defaults: '共享默认值（创建实例时确认，此页不修改）',
      loading: '读取中…',
      loadFailed: '读取失败',
      retry: '重试',
      saveFailed: '保存失败',
      enabled: '允许',
      invalidTimeout: '超时必须是不小于 1000 的整数',
      incompleteTitle: '安装不完整',
      incompleteHint: '缺少文件：',
      incompleteFix: '。请重新安装包含 skill-integrity.json 的版本（npm 5.1.2 或更新），或改用 GitHub / 本地目录安装。',
      aboutTitle: '这个插件是什么',
      positioning: '把 AI 的长任务变成「有合同、有版本、有证据」的串行作业流：断会话不丢进度，验收不靠印象。',
      problemsTitle: '解决的问题',
      problem1: '任务跨会话、跨天就丢了进度 → 进度写进实例文件，不依赖聊天记忆',
      problem2: '“做完了”没有证据，评审靠感觉 → 每张卡有合同、必验项和证据路径，独立 reviewer 逐项判定',
      problem3: '返工失控、越做越偏 → 首实现后默认最多返工 3 次，失败保留成果且不阻塞独立任务',
      problem4: 'agent 自己“授权” → 三种决策模式，授权边界写进合同，agent 不能冒充人类批准',
      fitTitle: '适合与不适合',
      fitGood: '适合：跨会话、多阶段、需要证据的工程任务（UI 改造、重构、迁移、批量修复），以及需要独立验收的交付。',
      fitBad: '不适合：一次性小改动、纯问答，或要求完全无人值守且不得留下失败记录的场景——本工作流如实记录失败，不掩盖。',
      usageTitle: '怎么用',
      usage1: '创建实例：让 agent 跑 sjs init --dry-run，确认目标、授权路径、决策模式与失败处置（模式没有默认值）。',
      usage2: '日常推进：说 route_key：意图（如 界面优化：推进），或让 agent 用 sjs context 读取当前任务。',
      usage3: '收口：实现 → 自检 → 独立 reviewer 验收 → 结案，并把有证据的经验沉淀下来。',
      usage4: '调参数：解释器、超时与额外 CLI 参数在本页调整，保存后下一次调用生效。',
      note1: '注意：不要把仓库同时复制进 ~/.dsh/skills，同名会遮蔽插件注册。',
      note2: '注意：首次安装后需要重启一次 Harness，插件页才会加载这段说明与参数区。',
      learnMore: '完整介绍',
      learnMoreZh: '中文说明',
      learnMoreEn: 'English guide',
    }

    const EN = {
      sectionLabel: 'Serial Jobs',
      summary: 'Interpreter, timeout and extra CLI arguments',
      title: 'Serial Job Session parameters',
      intro:
        'These parameters belong to the serial-job-session plugin installed here. The `sjs` tool reads them on every call, so a save applies immediately without restarting the Harness.',
      interpreter: 'Python interpreter',
      interpreterHint: 'Empty means auto-detect; only Python 3.11+ is accepted (2.x is rejected). Precedence: SJS_PYTHON → this setting → python3 / python / py.',
      interpreterPlaceholder: 'auto (python / python3 / py)',
      timeout: 'Call timeout (ms)',
      timeoutHint: '1000–600000. The SJS_TIMEOUT_MS environment variable overrides this setting.',
      extraArgs: 'Extra CLI arguments',
      extraArgsHint: 'One argument per line, appended to every sjs call, for example --append-library.',
      save: 'Save',
      saving: 'Saving…',
      saved: 'Saved',
      reset: 'Reset',
      probe: 'Check environment',
      probing: 'Checking…',
      probeOk: 'Environment ready',
      probeFail: 'Environment unavailable',
      info: 'Read-only information',
      repo: 'Repository',
      cli: 'CLI script',
      skill: 'Skill name',
      modelInvocation: 'On-demand model loading',
      settingsFile: 'Settings file',
      version: 'Version',
      defaults: 'Shared defaults (confirmed at creation; not edited here)',
      loading: 'Loading…',
      loadFailed: 'Read failed',
      retry: 'Retry',
      saveFailed: 'Save failed',
      enabled: 'allowed',
      invalidTimeout: 'Timeout must be an integer of at least 1000',
      incompleteTitle: 'Installation incomplete',
      incompleteHint: 'Missing files: ',
      incompleteFix: '. Reinstall a release that ships skill-integrity.json (npm 5.1.2 or newer), or use the GitHub / local-directory install.',
      aboutTitle: 'What this plugin is',
      positioning:
        'Turns a long AI task into a serial workflow with a contract, versions and evidence: a dropped session never loses progress, and acceptance rests on evidence, not impressions.',
      problemsTitle: 'Problems it solves',
      problem1: 'Progress dies with the session → it lives in the instance files, not in chat memory',
      problem2: '“Done” without evidence → every card carries a contract, mandatory checks and evidence paths, judged one by one by an independent reviewer',
      problem3: 'Runaway rework → at most three rework rounds after first implementation; exhausted work is preserved and never blocks independent tasks',
      problem4: 'Agents “authorizing” themselves → three decision modes, the authorization boundary lives in the contract, and no agent can impersonate human approval',
      fitTitle: 'When to use it',
      fitGood: 'Good fit: multi-session, multi-stage engineering work that needs evidence (UI rework, refactors, migrations, batch fixes) and deliveries requiring independent acceptance.',
      fitBad: 'Poor fit: one-shot small changes, pure Q&A, or fully unattended runs that must never record a failure — this workflow records failures truthfully instead of hiding them.',
      usageTitle: 'How to use it',
      usage1: 'Create an instance: have the agent run sjs init --dry-run and confirm goal, authorized paths, decision mode and failure handling (the mode has no default).',
      usage2: 'Work day to day: say route_key：意图 (for example 界面优化：推进), or have the agent read the current card with sjs context.',
      usage3: 'Close out: implement → self-check → independent reviewer acceptance → close, and record the evidence-backed lessons.',
      usage4: 'Tune it: interpreter, timeout and extra CLI arguments live in this page and apply on the next call.',
      note1: 'Note: do not also copy this repository into ~/.dsh/skills — a same-named entry shadows the bundle registration.',
      note2: 'Note: after the first installation the Harness needs one restart before this page and its settings load.',
      learnMore: 'Full introduction',
      learnMoreZh: '中文说明',
      learnMoreEn: 'English guide',
    }

    /** Styles rendered inside the component, so unmounting removes them. */
    const CSS = `
.sjs-panel { display: flex; flex-direction: column; gap: 18px; max-width: 760px; color: var(--dsw-alias-label-primary); font-size: 13px; }
.sjs-panel h2 { margin: 0; font-size: 16px; font-weight: 600; }
.sjs-intro { margin: 0; color: var(--dsw-alias-label-secondary); line-height: 1.6; }
.sjs-positioning { margin: 0; color: var(--dsw-alias-label-primary); line-height: 1.65; font-weight: 500; }
.sjs-subtitle { margin: 4px 0 0; font-size: 12px; font-weight: 600; color: var(--dsw-alias-label-secondary); }
.sjs-list { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 5px; line-height: 1.6; }
.sjs-link { color: var(--dsw-alias-brand-primary); text-decoration: none; }
.sjs-link:hover { text-decoration: underline; }
.sjs-group { display: flex; flex-direction: column; gap: 12px; padding: 16px; border: 1px solid var(--dsw-alias-border-l1); border-radius: 10px; background: var(--dsw-alias-bg-layer-1); }
.sjs-group-title { margin: 0 0 2px; font-size: 13px; font-weight: 600; }
.sjs-field { display: flex; flex-direction: column; gap: 6px; }
.sjs-field label { color: var(--dsw-alias-label-primary); font-weight: 500; }
.sjs-field input, .sjs-field textarea {
  box-sizing: border-box; width: 100%; padding: 7px 9px; border-radius: 6px;
  border: 1px solid var(--dsw-alias-border-l1); background: var(--dsw-alias-bg-base);
  color: var(--dsw-alias-label-primary); font: inherit; line-height: 1.5;
}
.sjs-field textarea { resize: vertical; min-height: 64px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.sjs-field input:focus, .sjs-field textarea:focus { outline: none; border-color: var(--dsw-alias-brand-primary); }
.sjs-hint { margin: 0; color: var(--dsw-alias-label-secondary); font-size: 12px; line-height: 1.5; }
.sjs-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.sjs-button {
  padding: 6px 14px; border-radius: 6px; border: 1px solid var(--dsw-alias-border-l2);
  background: transparent; color: var(--dsw-alias-label-primary); font: inherit; cursor: pointer;
}
.sjs-button:hover:not(:disabled) { border-color: var(--dsw-alias-brand-primary); }
.sjs-button:disabled { opacity: .55; cursor: default; }
.sjs-button-primary { border-color: var(--dsw-alias-brand-primary); background: var(--dsw-alias-brand-primary); color: #fff; }
.sjs-button-link { border-color: transparent; color: var(--dsw-alias-label-secondary); padding: 6px 4px; }
.sjs-status { font-size: 12px; }
.sjs-status-ok { color: var(--dsw-alias-state-success-primary); }
.sjs-status-error { color: var(--dsw-alias-state-error-primary); }
.sjs-status-warn { color: var(--dsw-alias-state-warn-primary); }
.sjs-probe { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border-radius: 8px; background: var(--dsw-alias-bg-layer-2); font-size: 12px; line-height: 1.55; }
.sjs-probe code, .sjs-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.sjs-meta { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 12px; }
.sjs-meta dt { color: var(--dsw-alias-label-secondary); }
.sjs-meta dd { margin: 0; color: var(--dsw-alias-label-primary); word-break: break-all; }
.sjs-pre { margin: 0; padding: 10px 12px; border-radius: 8px; background: var(--dsw-alias-bg-layer-2); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; line-height: 1.55; overflow: auto; max-height: 220px; }
.sjs-empty { margin: 0; color: var(--dsw-alias-label-secondary); }
`

    /**
     * Fetch one API document, turning transport and envelope failures into throws.
     *
     * @param {string} url endpoint.
     * @param {object} [init] fetch options.
     * @returns {Promise<Record<string, unknown>>} parsed JSON body.
     */
    async function callApi(url, init) {
      const response = await fetch(url, init)
      const text = await response.text()
      let body
      try {
        body = text.length > 0 ? JSON.parse(text) : {}
      } catch {
        throw new Error(`unexpected response from ${url}`)
      }
      if (!response.ok || body.ok === false) {
        throw new Error(typeof body.error === 'string' ? body.error : `request failed (${response.status})`)
      }
      return body
    }

    /**
     * Split the textarea into the stored argument list.
     *
     * @param {string} text textarea value.
     * @returns {string[]} trimmed non-empty arguments.
     */
    function splitArgs(text) {
      return String(text ?? '')
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line.length > 0)
    }

    /**
     * The settings panel itself.
     *
     * @param {{ t: (key: string) => string, view?: 'summary' | 'page', close?: () => void }} props owner props plus the local translator.
     * @returns {import('react').ReactElement} rendered panel.
     */
    function Panel(props) {
      const t = props.t
      const [state, setState] = React.useState(null)
      const [form, setForm] = React.useState({ python: '', timeoutMs: '120000', extraArgs: '' })
      const [loading, setLoading] = React.useState(true)
      const [busy, setBusy] = React.useState('')
      const [message, setMessage] = React.useState(null)
      const [probe, setProbe] = React.useState(null)
      const [error, setError] = React.useState('')

      const load = React.useCallback(() => {
        setLoading(true)
        setError('')
        callApi(API.state, { headers: { accept: 'application/json' } })
          .then((doc) => {
            setState(doc)
            setForm({
              python: doc.settings?.python ?? '',
              timeoutMs: String(doc.settings?.timeoutMs ?? 120000),
              extraArgs: (doc.settings?.extraArgs ?? []).join('\n'),
            })
            setLoading(false)
          })
          .catch((failure) => {
            setError(failure instanceof Error ? failure.message : String(failure))
            setLoading(false)
          })
      }, [])

      React.useEffect(() => {
        load()
      }, [load])

      const save = () => {
        const timeoutMs = Number(form.timeoutMs)
        if (!Number.isFinite(timeoutMs) || timeoutMs < 1000) {
          setMessage({ kind: 'error', text: t('invalidTimeout') })
          return
        }
        setBusy('save')
        setMessage(null)
        callApi(API.settings, {
          method: 'POST',
          headers: { 'content-type': 'application/json', accept: 'application/json' },
          body: JSON.stringify({
            python: form.python,
            timeoutMs,
            extraArgs: splitArgs(form.extraArgs),
          }),
        })
          .then((doc) => {
            setBusy('')
            setMessage({ kind: 'ok', text: t('saved') })
            if (state) setState({ ...state, settings: doc.settings, settingsPath: doc.settingsPath ?? state.settingsPath })
            if (Array.isArray(doc.settings?.extraArgs)) {
              setForm((current) => ({ ...current, extraArgs: doc.settings.extraArgs.join('\n') }))
            }
          })
          .catch((failure) => {
            setBusy('')
            setMessage({
              kind: 'error',
              text: `${t('saveFailed')}: ${failure instanceof Error ? failure.message : String(failure)}`,
            })
          })
      }

      const runProbe = () => {
        setBusy('probe')
        setProbe(null)
        callApi(API.probe, {
          method: 'POST',
          headers: { 'content-type': 'application/json', accept: 'application/json' },
          body: '{}',
        })
          .then((doc) => {
            setBusy('')
            setProbe(doc)
          })
          .catch((failure) => {
            setBusy('')
            setProbe({ ok: false, error: failure instanceof Error ? failure.message : String(failure) })
          })
      }

      const reset = () => {
        setForm({ python: '', timeoutMs: '120000', extraArgs: '' })
        setMessage(null)
        setProbe(null)
      }

      if (props.view === 'summary') {
        return h('span', { className: 'sjs-hint' }, t('summary'))
      }

      const heading = h('h2', null, t('title'))
      const intro = h('p', { className: 'sjs-intro' }, t('intro'))
      const style = h('style', null, CSS)
      const about = h(
        'section',
        { className: 'sjs-group' },
        h('h3', { className: 'sjs-group-title' }, t('aboutTitle')),
        h('p', { className: 'sjs-positioning' }, t('positioning')),
        h('h4', { className: 'sjs-subtitle' }, t('problemsTitle')),
        h(
          'ul',
          { className: 'sjs-list' },
          ['problem1', 'problem2', 'problem3', 'problem4'].map((key) => h('li', { key }, t(key))),
        ),
        h('h4', { className: 'sjs-subtitle' }, t('fitTitle')),
        h('ul', { className: 'sjs-list' }, h('li', null, t('fitGood')), h('li', null, t('fitBad'))),
        h('h4', { className: 'sjs-subtitle' }, t('usageTitle')),
        h(
          'ol',
          { className: 'sjs-list' },
          ['usage1', 'usage2', 'usage3', 'usage4'].map((key) => h('li', { key }, t(key))),
        ),
        h('p', { className: 'sjs-hint' }, t('note1')),
        h('p', { className: 'sjs-hint' }, t('note2')),
        h(
          'p',
          { className: 'sjs-hint' },
          `${t('learnMore')}：`,
          h(
            'a',
            {
              className: 'sjs-link',
              href: 'https://github.com/FMJ9922/serial-job-session/blob/main/dsh/INTRO.md',
              target: '_blank',
              rel: 'noreferrer',
            },
            t('learnMoreZh'),
          ),
          ' · ',
          h(
            'a',
            {
              className: 'sjs-link',
              href: 'https://github.com/FMJ9922/serial-job-session/blob/main/dsh/INTRO.en.md',
              target: '_blank',
              rel: 'noreferrer',
            },
            t('learnMoreEn'),
          ),
        ),
      )

      if (loading) {
        return h('div', { className: 'sjs-panel' }, style, heading, intro, about, h('p', { className: 'sjs-empty' }, t('loading')))
      }

      if (error) {
        return h(
          'div',
          { className: 'sjs-panel' },
          style,
          heading,
          intro,
          about,
          h('p', { className: 'sjs-status sjs-status-error' }, `${t('loadFailed')}: ${error}`),
          h('div', { className: 'sjs-actions' }, h('button', { type: 'button', className: 'sjs-button', onClick: load }, t('retry'))),
        )
      }

      const settings = state?.settings ?? {}
      const installation = state?.installation
      const installationWarning =
        installation && installation.ok === false
          ? h(
              'p',
              { className: 'sjs-status sjs-status-error' },
              `${t('incompleteTitle')}：${t('incompleteHint')}${(installation.missing ?? []).join('、')}${t('incompleteFix')}`,
            )
          : null
      const metaRows = [
        [t('repo'), state?.repoRoot],
        [t('cli'), state?.cliPath],
        [t('skill'), state?.skill?.name],
        [t('modelInvocation'), state?.skill?.modelInvocable ? t('enabled') : '—'],
        [t('settingsFile'), state?.settingsPath],
        [t('version'), state?.version],
      ].filter((row) => row[1])

      return h(
        'div',
        { className: 'sjs-panel' },
        style,
        heading,
        intro,
        installationWarning,
        about,
        h(
          'section',
          { className: 'sjs-group' },
          h('h3', { className: 'sjs-group-title' }, t('interpreter')),
          h(
            'div',
            { className: 'sjs-field' },
            h('label', { htmlFor: 'sjs-python' }, t('interpreter')),
            h('input', {
              id: 'sjs-python',
              type: 'text',
              spellCheck: false,
              value: form.python,
              placeholder: state?.env?.SJS_PYTHON || t('interpreterPlaceholder'),
              onChange: (event) => setForm({ ...form, python: event.target.value }),
            }),
            h('p', { className: 'sjs-hint' }, t('interpreterHint')),
          ),
          h(
            'div',
            { className: 'sjs-field' },
            h('label', { htmlFor: 'sjs-timeout' }, t('timeout')),
            h('input', {
              id: 'sjs-timeout',
              type: 'number',
              min: 1000,
              max: 600000,
              step: 1000,
              value: form.timeoutMs,
              onChange: (event) => setForm({ ...form, timeoutMs: event.target.value }),
            }),
            h('p', { className: 'sjs-hint' }, t('timeoutHint')),
          ),
          h(
            'div',
            { className: 'sjs-field' },
            h('label', { htmlFor: 'sjs-extra' }, t('extraArgs')),
            h('textarea', {
              id: 'sjs-extra',
              rows: 3,
              spellCheck: false,
              value: form.extraArgs,
              onChange: (event) => setForm({ ...form, extraArgs: event.target.value }),
            }),
            h('p', { className: 'sjs-hint' }, t('extraArgsHint')),
          ),
          h(
            'div',
            { className: 'sjs-actions' },
            h(
              'button',
              {
                type: 'button',
                className: 'sjs-button sjs-button-primary',
                disabled: busy === 'save',
                onClick: save,
              },
              busy === 'save' ? t('saving') : t('save'),
            ),
            h(
              'button',
              {
                type: 'button',
                className: 'sjs-button',
                disabled: busy === 'probe',
                onClick: runProbe,
              },
              busy === 'probe' ? t('probing') : t('probe'),
            ),
            h('button', { type: 'button', className: 'sjs-button sjs-button-link', onClick: reset }, t('reset')),
            message
              ? h(
                  'span',
                  {
                    className:
                      message.kind === 'ok'
                        ? 'sjs-status sjs-status-ok'
                        : 'sjs-status sjs-status-error',
                  },
                  message.text,
                )
              : null,
          ),
          probe
            ? h(
                'div',
                { className: 'sjs-probe' },
                h(
                  'span',
                  { className: probe.ok ? 'sjs-status-ok' : 'sjs-status-error' },
                  probe.ok ? t('probeOk') : t('probeFail'),
                ),
                probe.ok
                  ? h(React.Fragment, null,
                      h('span', null, h('span', { className: 'sjs-mono' }, String(probe.interpreter ?? ''))),
                      h('span', null, h('span', { className: 'sjs-mono' }, String(probe.python ?? ''))),
                    )
                  : h('span', null, String(probe.error ?? '')),
              )
            : null,
          h('p', { className: 'sjs-hint' }, settings.extraArgs?.length
            ? `${t('extraArgs')}: ${settings.extraArgs.join(' ')}`
            : ''),
        ),
        h(
          'section',
          { className: 'sjs-group' },
          h('h3', { className: 'sjs-group-title' }, t('info')),
          h(
            'dl',
            { className: 'sjs-meta' },
            metaRows.flatMap(([label, value]) => [
              h('dt', { key: `${label}-label` }, label),
              h('dd', { key: `${label}-value` }, String(value)),
            ]),
          ),
        ),
        state?.defaults
          ? h(
              'section',
              { className: 'sjs-group' },
              h('h3', { className: 'sjs-group-title' }, t('defaults')),
              h('pre', { className: 'sjs-pre' }, JSON.stringify(state.defaults, null, 2)),
            )
          : null,
      )
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        let translate = (key) => ZH[key] ?? key
        try {
          const locale = ctx.get('locale')
          if (locale && typeof locale.register === 'function' && typeof locale.bind === 'function') {
            locale.register(LOCALE_NS, 'zh', ZH)
            locale.register(LOCALE_NS, 'en', EN)
            const bound = locale.bind(LOCALE_NS)
            translate = (key) => {
              const value = bound(key)
              return typeof value === 'string' && value.length > 0 ? value : ZH[key] ?? key
            }
          }
        } catch (failure) {
          console.error('serial-job-session locale registration failed', failure)
        }

        const PanelView = (props) => h(Panel, { ...props, t: translate })

        ctx.slots.inject('settings.section', () =>
          ctx.slots.register(
            {
              name: 'settings.section',
              id: 'serial-job-session',
              order: 40,
              label: () => translate('sectionLabel'),
            },
            PanelView,
          ),
        )

        ctx.slots.inject('plugins.bundle.config', () =>
          ctx.slots.register(
            {
              name: 'plugins.bundle.config',
              key: 'dsh-serial-job-session',
            },
            PanelView,
          ),
        )
      },
    }
  },
})
