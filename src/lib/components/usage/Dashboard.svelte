<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { config } from '$lib/stores';
	import { getUsageOverview, type UsageOverview } from '$lib/apis/langfuse';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { formatNumber } from '$lib/utils';

	const i18n: any = getContext('i18n');

	let loading = true;
	let data: UsageOverview | null = null;
	let days = 30;
	let now = new Date();

	const fmtCost = (n: number) => {
		if (!n) return '$0';
		if (n < 0.0001) return '<$0.0001';
		if (n < 0.01) return `$${n.toFixed(4)}`;
		if (n < 1) return `$${n.toFixed(3)}`;
		return `$${n.toFixed(2)}`;
	};
	const fmtLatency = (n: number) => (n ? `${n.toFixed(1)}s` : '—');
	const fmtDate = (d: string) => {
		const dt = new Date(d);
		return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	};

	const load = async () => {
		loading = true;
		const token = localStorage.token;
		try {
			data = await getUsageOverview(token, days);
		} catch (e) {
			data = null;
		}
		loading = false;
		now = new Date();
	};

	onMount(async () => {
		if (!($config?.features?.enable_langfuse ?? false)) {
			await goto('/');
			return;
		}
		await load();
	});

	$: maxModelTokens = Math.max(1, ...(data?.byModel ?? []).map((m) => m.tokens));
	$: maxDailyTokens = Math.max(1, ...(data?.daily ?? []).map((d) => d.tokens));
	$: hasData = data && (data.totals.requests > 0 || data.totals.tokens > 0);
	$: pricedModels = (data?.byModel ?? []).filter((m) => m.cost > 0).length;
	$: unpricedModels = (data?.byModel ?? []).filter((m) => m.cost === 0 && m.tokens > 0);
</script>

<div class="flex flex-col w-full h-full px-4 md:px-8 py-6 overflow-y-auto">
	<!-- Header -->
	<div class="flex items-center justify-between mb-1">
		<div>
			<div class="text-2xl font-semibold tracking-tight">{$i18n.t('My Usage')}</div>
			<div class="text-xs text-gray-500 mt-0.5">
				{$i18n.t('Last updated')}
				{now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
			</div>
		</div>

		<div class="flex items-center gap-2">
			<button
				class="text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 px-2 py-1 rounded-md transition"
				on:click={load}
				title={$i18n.t('Refresh')}
				aria-label="Refresh"
			>
				↻
			</button>
			<select
				class="text-sm bg-transparent border border-gray-200 dark:border-gray-800 rounded-lg px-2.5 py-1 outline-none cursor-pointer hover:border-gray-300 dark:hover:border-gray-700"
				bind:value={days}
				on:change={load}
			>
				<option value={7}>{$i18n.t('Last 7 days')}</option>
				<option value={30}>{$i18n.t('Last 30 days')}</option>
				<option value={90}>{$i18n.t('Last 90 days')}</option>
			</select>
		</div>
	</div>

	<div class="mt-6 flex-1">
		{#if loading}
			<div class="flex justify-center items-center h-64"><Spinner /></div>
		{:else if !hasData}
			<div class="flex flex-col items-center justify-center h-64 text-center max-w-md mx-auto">
				<div class="text-base font-medium text-gray-600 dark:text-gray-300 mb-2">
					{$i18n.t('No usage yet')}
				</div>
				<div class="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
					{$i18n.t(
						'Send a chat with any model and your usage will appear here within a few seconds.'
					)}
				</div>
			</div>
		{:else}
			<!-- KPI cards -->
			<div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-8">
				<div class="bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-850 rounded-2xl p-5">
					<div class="text-xs uppercase tracking-wider text-gray-500 mb-2">{$i18n.t('Tokens')}</div>
					<div class="text-2xl font-semibold tabular-nums">{formatNumber(data.totals.tokens)}</div>
				</div>
				<div class="bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-850 rounded-2xl p-5">
					<div class="text-xs uppercase tracking-wider text-gray-500 mb-2">{$i18n.t('Cost')}</div>
					<div class="text-2xl font-semibold tabular-nums">{fmtCost(data.totals.cost)}</div>
					{#if unpricedModels.length > 0}
						<div class="text-[10px] text-amber-600 dark:text-amber-500 mt-1">
							{unpricedModels.length}
							{$i18n.t('model(s) not priced')}
						</div>
					{/if}
				</div>
				<div class="bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-850 rounded-2xl p-5">
					<div class="text-xs uppercase tracking-wider text-gray-500 mb-2">{$i18n.t('Requests')}</div>
					<div class="text-2xl font-semibold tabular-nums">{formatNumber(data.totals.requests)}</div>
				</div>
				<div class="bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-850 rounded-2xl p-5">
					<div class="text-xs uppercase tracking-wider text-gray-500 mb-2">
						{$i18n.t('Avg latency')}
					</div>
					<div class="text-2xl font-semibold tabular-nums">{fmtLatency(data.totals.avgLatency)}</div>
				</div>
			</div>

			<!-- Daily activity -->
			{#if data.daily && data.daily.length > 0}
				<div class="mb-8">
					<div class="flex items-center justify-between mb-3">
						<div class="text-sm font-medium">{$i18n.t('Daily activity')}</div>
						<div class="text-xs text-gray-500">{data.daily.length} {$i18n.t('days')}</div>
					</div>
					<div
						class="bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-850 rounded-2xl p-4"
					>
						<div class="flex items-end gap-1 h-24">
							{#each data.daily as d}
								<div
									class="flex-1 min-w-[4px] bg-gray-300 dark:bg-gray-700 hover:bg-black dark:hover:bg-white rounded-sm transition-colors cursor-help group relative"
									style="height: {Math.max(2, (d.tokens / maxDailyTokens) * 96)}px"
									title={`${fmtDate(d.date)}: ${formatNumber(d.tokens)} tokens · ${fmtCost(d.cost)} · ${d.requests} requests`}
								></div>
							{/each}
						</div>
						<div class="flex justify-between mt-2 text-[10px] text-gray-500">
							<div>{fmtDate(data.daily[0].date)}</div>
							<div>{fmtDate(data.daily[data.daily.length - 1].date)}</div>
						</div>
					</div>
				</div>
			{/if}

			<!-- Usage by model -->
			<div class="mb-6">
				<div class="flex items-center justify-between mb-3">
					<div class="text-sm font-medium">{$i18n.t('Usage by model')}</div>
					<div class="text-xs text-gray-500">{data.byModel.length} {$i18n.t('models')}</div>
				</div>
				<div
					class="bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-850 rounded-2xl overflow-hidden"
				>
					<table class="w-full text-sm">
						<thead>
							<tr class="text-[11px] uppercase tracking-wider text-gray-500 border-b border-gray-100 dark:border-gray-850">
								<th class="text-left font-medium px-4 py-3">{$i18n.t('Model')}</th>
								<th class="text-right font-medium px-4 py-3">{$i18n.t('Tokens')}</th>
								<th class="text-right font-medium px-4 py-3">{$i18n.t('Cost')}</th>
								<th class="text-right font-medium px-4 py-3">{$i18n.t('Requests')}</th>
							</tr>
						</thead>
						<tbody>
							{#each data.byModel as m}
								<tr
									class="border-b border-gray-100 dark:border-gray-850 last:border-0 hover:bg-gray-100/50 dark:hover:bg-gray-900/30 transition"
								>
									<td class="px-4 py-3">
										<div class="flex items-center gap-3">
											<div class="font-mono text-xs truncate max-w-[260px]" title={m.model}>
												{m.model}
											</div>
										</div>
										<div class="mt-1.5 bg-gray-200 dark:bg-gray-800 rounded-full h-1 overflow-hidden">
											<div
												class="bg-black/70 dark:bg-white/80 h-full rounded-full transition-all"
												style="width: {(m.tokens / maxModelTokens) * 100}%"
											></div>
										</div>
									</td>
									<td class="text-right tabular-nums px-4 py-3 align-top">
										{formatNumber(m.tokens)}
									</td>
									<td
										class="text-right tabular-nums px-4 py-3 align-top"
										class:text-amber-600={m.cost === 0 && m.tokens > 0}
									>
										{m.cost === 0 && m.tokens > 0 ? '—' : fmtCost(m.cost)}
									</td>
									<td class="text-right tabular-nums text-gray-500 px-4 py-3 align-top">
										{formatNumber(m.requests)}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>

				{#if unpricedModels.length > 0}
					<div
						class="mt-3 text-xs text-gray-500 dark:text-gray-400 leading-relaxed bg-amber-50 dark:bg-amber-950/20 border border-amber-100 dark:border-amber-950/40 rounded-xl px-4 py-3"
					>
						<span class="font-medium text-amber-800 dark:text-amber-400">{$i18n.t('Cost showing as')} —</span>
						{$i18n.t(
							"means the model isn't in Langfuse's pricing catalogue. Register it under Langfuse → Settings → Models with the provider's per-token rate to start tracking spend. Token counts above are accurate regardless."
						)}
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>
