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

	const fmtCost = (n: number) => (n ? `$${n.toFixed(n < 0.01 ? 4 : 2)}` : '$0');
	const fmtLatency = (n: number) => (n ? `${n.toFixed(1)}s` : '—');

	const load = async () => {
		loading = true;
		const token = localStorage.token;
		try {
			data = await getUsageOverview(token, days);
		} catch (e) {
			data = null;
		}
		loading = false;
	};

	onMount(async () => {
		if (!($config?.features?.enable_langfuse ?? false)) {
			await goto('/');
			return;
		}
		await load();
	});

	$: maxModelTokens = Math.max(1, ...(data?.byModel ?? []).map((m) => m.tokens));
</script>

<div class="flex flex-col w-full h-full p-4 md:p-6 overflow-y-auto">
	<div class="flex items-center justify-between mb-4">
		<div class="text-2xl font-medium">{$i18n.t('My Usage')}</div>
		<select
			class="text-sm bg-transparent border border-gray-100 dark:border-gray-850 rounded-lg px-2 py-1 outline-none"
			bind:value={days}
			on:change={load}
		>
			<option value={7}>7d</option>
			<option value={30}>30d</option>
			<option value={90}>90d</option>
		</select>
	</div>

	{#if loading}
		<div class="flex justify-center items-center h-40"><Spinner /></div>
	{:else if !data || (data.totals.requests === 0 && data.totals.tokens === 0)}
		<div class="flex justify-center items-center h-40 text-gray-500 text-sm">
			{$i18n.t('No usage data yet')}
		</div>
	{:else}
		<div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
			{#each [['Tokens', formatNumber(data.totals.tokens)], ['Cost', fmtCost(data.totals.cost)], ['Requests', formatNumber(data.totals.requests)], ['Avg latency', fmtLatency(data.totals.avgLatency)]] as [label, value]}
				<div class="bg-gray-50 dark:bg-gray-850 rounded-2xl p-4">
					<div class="text-xs text-gray-500 mb-1">{$i18n.t(label)}</div>
					<div class="text-xl font-medium">{value}</div>
				</div>
			{/each}
		</div>

		<div class="text-sm font-medium mb-2">{$i18n.t('Usage by model')}</div>
		<div class="flex flex-col gap-2 mb-6">
			{#each data.byModel as m}
				<div class="flex items-center gap-3 text-sm">
					<div class="w-40 truncate text-gray-600 dark:text-gray-300" title={m.model}>
						{m.model}
					</div>
					<div class="flex-1 bg-gray-100 dark:bg-gray-800 rounded-full h-2.5 overflow-hidden">
						<div
							class="bg-black dark:bg-white h-full rounded-full"
							style="width: {(m.tokens / maxModelTokens) * 100}%"
						></div>
					</div>
					<div class="w-24 text-right tabular-nums">{formatNumber(m.tokens)}</div>
					<div class="w-20 text-right tabular-nums text-gray-500">{fmtCost(m.cost)}</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
