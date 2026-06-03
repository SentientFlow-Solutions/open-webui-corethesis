<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import { getChatUsage, type ChatUsage } from '$lib/apis/langfuse';

	const i18n: any = getContext('i18n');

	export let chatId: string = '';
	export let history: { messages: Record<string, any> } = { messages: {} };
	export let temporary: boolean = false;

	let usage: ChatUsage | null = null;

	// Instant, local token + turn count (masks Langfuse ingestion lag).
	$: localTokens = Object.values(history?.messages ?? {}).reduce((sum: number, m: any) => {
		const u = m?.usage;
		if (!u) return sum;
		const total =
			u.total_tokens ??
			(u.input_tokens ?? u.prompt_tokens ?? 0) + (u.output_tokens ?? u.completion_tokens ?? 0);
		return sum + (Number(total) || 0);
	}, 0);
	$: localTurns = Object.values(history?.messages ?? {}).filter(
		(m: any) => m?.role === 'assistant' && m?.usage
	).length;

	$: enabled = ($config?.features?.enable_langfuse ?? false) && !!chatId && !temporary;

	const fmtNum = (n: number) => new Intl.NumberFormat().format(n);
	const fmtCost = (n: number) => (n ? `$${n.toFixed(n < 0.01 ? 4 : 2)}` : '$0');

	let lastFetched = '';
	const refresh = async () => {
		if (!enabled) return;
		try {
			usage = await getChatUsage(localStorage.token, chatId);
		} catch {
			/* keep showing local values */
		}
	};

	// Refetch when the chat changes; re-fetch shortly after a turn finishes
	// (Langfuse ingestion lag) by reacting to the local turn count.
	$: if (enabled && chatId !== lastFetched) {
		lastFetched = chatId;
		usage = null;
		refresh();
	}
	$: if (enabled && localTurns) {
		setTimeout(refresh, 4000);
	}

	$: shownTokens = localTokens;
	$: shownTurns = usage?.turns ?? localTurns;
</script>

{#if enabled && (shownTokens > 0 || shownTurns > 0)}
	<div
		class="mx-auto w-full max-w-6xl px-3.5 pb-1 flex items-center justify-center gap-2 text-xs text-gray-400 dark:text-gray-500 select-none"
	>
		<svg
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="currentColor"
			class="size-3.5"
		>
			<path
				d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"
			/>
		</svg>
		<span class="tabular-nums">{fmtNum(shownTokens)} {$i18n.t('tokens')}</span>
		{#if usage}
			<span>·</span><span class="tabular-nums">{fmtCost(usage.cost)}</span>
			<span>·</span><span class="tabular-nums">{shownTurns} {$i18n.t('turns')}</span>
			{#if usage.avgLatency}
				<span>·</span><span class="tabular-nums"
					>{usage.avgLatency.toFixed(1)}s {$i18n.t('avg')}</span
				>
			{/if}
		{:else}
			<span>·</span><span class="tabular-nums">{shownTurns} {$i18n.t('turns')}</span>
		{/if}
	</div>
{/if}
