/**
 * Daily Life Dashboard Extension for Hermes Agent
 * Plain IIFE compatible with Hermes Plugin SDK.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var PLUGINS = window.__HERMES_PLUGINS__;
  if (!SDK || !PLUGINS || !SDK.React) return;

  var React = SDK.React;
  var C = SDK.components || {};
  var H = SDK.hooks || {};
  var U = SDK.utils || {};

  var Card = C.Card || "div";
  var CardHeader = C.CardHeader || "div";
  var CardTitle = C.CardTitle || "div";
  var CardContent = C.CardContent || "div";
  var Badge = C.Badge || "span";
  var Button = C.Button || "button";
  var Input = C.Input || "input";

  var useState = H.useState || React.useState;
  var useEffect = H.useEffect || React.useEffect;
  var useCallback = H.useCallback || React.useCallback;
  var h = React.createElement;

  var API_BASE = "/api/plugins/daily-life";

  function fetchJSON(url, opts) {
    if (SDK.fetchJSON) return SDK.fetchJSON(url, opts);
    return window.fetch(url, opts).then(function (r) {
      if (!r.ok) throw new Error(r.status + " " + r.statusText);
      return r.json();
    });
  }

  function api(path, opts) {
    return fetchJSON(API_BASE + path, opts);
  }

  function DailyLifePage() {
    var _p = useState("default"); var profile = _p[0], setProfile = _p[1];
    var _l = useState(false); var loading = _l[0], setLoading = _l[1];
    var _e = useState(null); var error = _e[0], setError = _e[1];
    var _s = useState(null); var status = _s[0], setStatus = _s[1];
    var _pl = useState(null); var plan = _pl[0], setPlan = _pl[1];
    var _br = useState(null); var briefing = _br[0], setBriefing = _br[1];
    var _in = useState(null); var insights = _in[0], setInsights = _in[1];
    var _op = useState([]); var opps = _op[0], setOpps = _op[1];
    var _rv = useState(null); var review = _rv[0], setReview = _rv[1];

    var loadAll = useCallback(function (p) {
      setLoading(true);
      setError(null);
      var pid = p || profile;

      api("/status?profile=" + encodeURIComponent(pid))
        .then(function (res) { setStatus(res); })
        .catch(function (err) { setError(err.message || String(err)); });

      api("/plan/today?profile=" + encodeURIComponent(pid))
        .then(function (res) { setPlan(res && res.plan ? res.plan : null); })
        .catch(function () { setPlan(null); });

      api("/briefing/today?profile=" + encodeURIComponent(pid))
        .then(function (res) { setBriefing(res && res.briefing ? res.briefing : null); })
        .catch(function () { setBriefing(null); });

      api("/learning/insights?profile=" + encodeURIComponent(pid))
        .then(function (res) { setInsights(res); })
        .catch(function () { setInsights(null); });

      api("/opportunities?profile=" + encodeURIComponent(pid))
        .then(function (res) { setOpps(res && res.opportunities ? res.opportunities : []); })
        .catch(function () { setOpps([]); });

      api("/review/today?profile=" + encodeURIComponent(pid))
        .then(function (res) { setReview(res && res.review ? res.review : null); })
        .catch(function () { setReview(null); })
        .finally(function () { setLoading(false); });
    }, [profile]);

    useEffect(function () {
      loadAll(profile);
    }, [profile, loadAll]);

    function triggerCycle() {
      setLoading(true);
      fetchJSON(API_BASE + "/cycle/trigger", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true }),
      })
        .then(function () { loadAll(profile); })
        .catch(function (err) { setError("Trigger error: " + (err.message || String(err))); setLoading(false); });
    }

    var brain = status && status.brain ? status.brain : {};
    var verdict = brain.verdict || "proceed";
    var verdictColor = verdict === "proceed" ? "bg-emerald-500/10 text-emerald-600 border-emerald-500/20" :
                       verdict === "caution" ? "bg-amber-500/10 text-amber-600 border-amber-500/20" :
                       "bg-rose-500/10 text-rose-600 border-rose-500/20";

    var tasks = plan && plan.tasks ? plan.tasks : [];
    var calibs = insights && insights.learned_calibrations ? insights.learned_calibrations : {};
    var shared = insights && insights.shared_insights ? insights.shared_insights : [];

    return h("div", { className: "flex flex-col gap-5 p-4 max-w-7xl mx-auto" },
      // Header Bar
      h("div", { className: "flex items-center justify-between border-b pb-4" },
        h("div", { className: "flex items-center gap-3" },
          h("h1", { className: "text-2xl font-bold tracking-tight" }, "Daily Life Intelligence"),
          h(Badge, { variant: "outline" }, "v0.1.0"),
          loading ? h("span", { className: "text-xs text-muted-foreground animate-pulse" }, "Syncing...") : null
        ),
        h("div", { className: "flex items-center gap-2" },
          h("span", { className: "text-xs font-medium text-muted-foreground" }, "Profile:"),
          h(Input, {
            value: profile,
            onChange: function (e) { setProfile(e.target.value); },
            className: "w-28 h-8 text-xs font-mono",
            placeholder: "default",
          }),
          h(Button, { onClick: triggerCycle, disabled: loading, className: "h-8 px-3 text-xs bg-primary text-primary-foreground cursor-pointer" }, "Trigger Cycle")
        )
      ),

      error ? h("div", { className: "p-3 rounded border border-red-300 bg-red-50 text-red-700 text-xs flex justify-between" },
        h("span", null, error),
        h("button", { onClick: function () { setError(null); }, className: "underline text-xs" }, "Dismiss")
      ) : null,

      // Top Row: Brain Interoception & Daily Focus Summary
      h("div", { className: "grid grid-cols-1 md:grid-cols-3 gap-4" },
        // Card 1: Cognitive State (Brain)
        h(Card, null,
          h(CardHeader, { className: "pb-2" },
            h("div", { className: "flex items-center justify-between" },
              h(CardTitle, { className: "text-sm font-semibold" }, "Cognitive State (Brain)"),
              h(Badge, { className: "border px-2 py-0.5 text-xs font-mono uppercase " + verdictColor }, verdict)
            )
          ),
          h(CardContent, { className: "flex flex-col gap-3 text-xs" },
            h("div", null,
              h("div", { className: "flex justify-between text-muted-foreground mb-1" },
                h("span", null, "Energy:"),
                h("span", { className: "font-mono font-medium" }, Math.round((brain.energy || 0.8) * 100) + "%")
              ),
              h("div", { className: "w-full bg-secondary h-2 rounded-full overflow-hidden" },
                h("div", { className: "bg-emerald-500 h-full", style: { width: Math.round((brain.energy || 0.8) * 100) + "%" } })
              )
            ),
            h("div", null,
              h("div", { className: "flex justify-between text-muted-foreground mb-1" },
                h("span", null, "Fatigue:"),
                h("span", { className: "font-mono font-medium" }, Math.round((brain.fatigue || 0.2) * 100) + "%")
              ),
              h("div", { className: "w-full bg-secondary h-2 rounded-full overflow-hidden" },
                h("div", { className: "bg-amber-500 h-full", style: { width: Math.round((brain.fatigue || 0.2) * 100) + "%" } })
              )
            ),
            h("div", { className: "pt-1 border-t text-muted-foreground flex justify-between" },
              h("span", null, "Circadian Phase:"),
              h("span", { className: "font-mono font-medium uppercase text-foreground" }, brain.circadian_phase || "active")
            )
          )
        ),

        // Card 2: Morning Briefing Overview
        h(Card, null,
          h(CardHeader, { className: "pb-2" },
            h(CardTitle, { className: "text-sm font-semibold" }, "Morning Briefing")
          ),
          h(CardContent, { className: "flex flex-col gap-2 text-xs" },
            !briefing ? h("p", { className: "text-muted-foreground" }, "No briefing snapshot recorded for today.") :
            h("div", { className: "flex flex-col gap-2" },
              h("div", { className: "flex justify-between" },
                h("span", { className: "text-muted-foreground" }, "Calendar Events:"),
                h("span", { className: "font-mono font-medium" }, (briefing.calendar_events || []).length)
              ),
              h("div", { className: "flex justify-between" },
                h("span", { className: "text-muted-foreground" }, "Pending Backlog:"),
                h("span", { className: "font-mono font-medium" }, (briefing.pending_tasks || []).length)
              ),
              h("div", { className: "flex justify-between" },
                h("span", { className: "text-muted-foreground" }, "Unread Newsletters:"),
                h("span", { className: "font-mono font-medium" }, (briefing.unread_newsletters || []).length)
              ),
              briefing.degraded_sources && briefing.degraded_sources.length > 0 ?
                h("div", { className: "mt-1 p-1.5 rounded bg-amber-500/10 border border-amber-500/20 text-amber-700 text-[11px]" },
                  "Degraded: " + briefing.degraded_sources.join(", ")
                ) : null
            )
          )
        ),

        // Card 3: Proactive Opportunities
        h(Card, null,
          h(CardHeader, { className: "pb-2" },
            h(CardTitle, { className: "text-sm font-semibold" }, "Proactive Opportunities")
          ),
          h(CardContent, { className: "flex flex-col gap-2 text-xs" },
            opps.length === 0 ? h("p", { className: "text-muted-foreground" }, "No repetitive task patterns detected yet.") :
            opps.slice(0, 2).map(function (op, i) {
              return h("div", { key: i, className: "p-2 rounded border bg-secondary/30 flex flex-col gap-1" },
                h("span", { className: "font-semibold text-foreground" }, op.activity_type),
                h("span", { className: "text-muted-foreground text-[11px]" }, op.recommendation)
              );
            })
          )
        )
      ),

      // Middle Row: Today's Active Plan & Scheduled Tasks
      h(Card, null,
        h(CardHeader, null,
          h("div", { className: "flex items-center justify-between" },
            h(CardTitle, { className: "text-base font-semibold" }, "Today's Agenda & Time-Blocks"),
            h(Badge, { variant: "outline" }, tasks.length + " tasks")
          )
        ),
        h(CardContent, null,
          tasks.length === 0 ? h("p", { className: "text-xs text-muted-foreground" }, "No tasks scheduled for today.") :
          h("div", { className: "flex flex-col gap-2" },
            tasks.map(function (t, i) {
              var targetBadgeClass = t.execution_target === "camoufox_scenario" ? "bg-purple-500/10 text-purple-600 border-purple-500/20" :
                                     t.execution_target === "gmail_triage" ? "bg-red-500/10 text-red-600 border-red-500/20" :
                                     t.execution_target === "tebar_publish" ? "bg-blue-500/10 text-blue-600 border-blue-500/20" :
                                     "bg-zinc-500/10 text-zinc-600 border-zinc-500/20";
              return h("div", { key: t.task_id || i, className: "p-3 rounded border flex items-center justify-between text-xs" },
                h("div", { className: "flex items-center gap-2" },
                  h(Badge, { className: "border text-[10px] font-mono " + targetBadgeClass }, t.execution_target || "kanban"),
                  h("span", { className: "font-medium text-foreground" }, t.title)
                ),
                h("div", { className: "flex items-center gap-3 text-muted-foreground font-mono" },
                  h("span", null, "Est: " + (t.estimated_duration_min || 30) + "m"),
                  h("span", { className: "text-[11px] px-1.5 py-0.5 rounded bg-secondary" }, "P" + (t.priority || 2))
                )
              );
            })
          )
        )
      ),

      // Bottom Row: Learning Calibration & Published Insights
      h("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-4" },
        h(Card, null,
          h(CardHeader, null,
            h(CardTitle, { className: "text-sm font-semibold" }, "Learned Duration Multipliers (N ≥ 7)")
          ),
          h(CardContent, null,
            Object.keys(calibs).length === 0 ? h("p", { className: "text-xs text-muted-foreground" }, "Insufficient historical telemetry samples to calibrate.") :
            h("div", { className: "flex flex-col gap-1 text-xs" },
              Object.keys(calibs).map(function (k) {
                var c = calibs[k];
                return h("div", { key: k, className: "flex justify-between py-1 border-b text-xs font-mono" },
                  h("span", { className: "truncate max-w-[200px]" }, k),
                  h("span", { className: "font-semibold" }, c.duration_multiplier + "x (N=" + c.sample_count + ")")
                );
              })
            )
          )
        ),

        h(Card, null,
          h(CardHeader, null,
            h(CardTitle, { className: "text-sm font-semibold" }, "Published Shared Insights (N ≥ 30)")
          ),
          h(CardContent, null,
            shared.length === 0 ? h("p", { className: "text-xs text-muted-foreground" }, "No shared insights published yet.") :
            h("div", { className: "flex flex-col gap-2 text-xs" },
              shared.map(function (s, i) {
                return h("div", { key: s.id || i, className: "p-2 rounded border bg-secondary/20 flex flex-col gap-1" },
                  h("div", { className: "flex justify-between text-[11px] font-mono text-muted-foreground" },
                    h("span", null, s.category),
                    h("span", null, "Conf: " + s.confidence_score)
                  ),
                  h("span", { className: "text-foreground" }, s.finding)
                );
              })
            )
          )
        )
      )
    );
  }

  PLUGINS.register("daily-life", DailyLifePage);
})();
