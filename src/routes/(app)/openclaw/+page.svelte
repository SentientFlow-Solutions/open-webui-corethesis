<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { getOpenclawHealth, listOpenclawAgents } from '$lib/apis/openclaw';
	import { mobile, showSidebar, user } from '$lib/stores';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	let loading = true;
	let agents = [];
	let health = null;
	let errorMessage = '';

	const load = async () => {
		loading = true;
		errorMessage = '';
		try {
			const [h, a] = await Promise.all([
				getOpenclawHealth(localStorage.token).catch((e) => ({
					ok: false,
					binary: 'openclaw',
					error: e?.message ?? String(e)
				})),
				listOpenclawAgents(localStorage.token).catch((e) => {
					errorMessage = e?.message ?? String(e);
					return [];
				})
			]);
			health = h;
			agents = a ?? [];
		} finally {
			loading = false;
		}
	};

	const openAgent = (agentId) => {
		goto(`/openclaw/${encodeURIComponent(agentId)}`);
	};

	onMount(load);
</script>

<svelte:head>
	<title>OpenClaw</title>
</svelte:head>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<nav class="px-2 pt-1.5 backdrop-blur-xl w-full drag-region">
		<div class="flex items-center">
			{#if $mobile}
				<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center">
					<button
						class="cursor-pointer p-1.5 flex rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={() => showSidebar.set(!$showSidebar)}
					>
						<div class="self-center">
							<Sidebar className="size-5" />
						</div>
					</button>
				</div>
			{/if}
			<div class="flex items-center gap-2 px-2 py-1">
				<span class="text-lg font-medium">OpenClaw</span>
				{#if health}
					{#if health.ok}
						<Tooltip content={`Gateway OK${health.version ? ' — ' + health.version : ''}`}>
							<span class="inline-block size-2 rounded-full bg-green-500"></span>
						</Tooltip>
					{:else}
						<Tooltip content={health.error ?? 'Gateway unreachable'}>
							<span class="inline-block size-2 rounded-full bg-red-500"></span>
						</Tooltip>
					{/if}
				{/if}
			</div>
		</div>
	</nav>

	<div class="flex-1 overflow-y-auto px-4 sm:px-8 py-6">
		<div class="max-w-4xl mx-auto">
			<h1 class="text-2xl font-semibold mb-1">Pick an agent</h1>
			<p class="text-sm text-gray-500 dark:text-gray-400 mb-6">
				Self-hosted OpenClaw agents on this server. Sessions persist per-agent.
			</p>

			{#if loading}
				<div class="text-sm text-gray-500">Loading agents…</div>
			{:else if errorMessage}
				<div
					class="rounded-xl border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 px-4 py-3 text-sm text-red-800 dark:text-red-200"
				>
					<div class="font-medium mb-1">Could not load agents</div>
					<div class="opacity-90">{errorMessage}</div>
					<button
						class="mt-2 text-xs underline opacity-80 hover:opacity-100"
						on:click={load}
					>
						Retry
					</button>
				</div>
			{:else if agents.length === 0}
				<div class="text-sm text-gray-500">
					No agents configured on the gateway. Add one with
					<code class="bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded">openclaw agents add</code>
					on the server.
				</div>
			{:else}
				<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
					{#each agents as agent (agent.id)}
						<button
							class="text-left rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-850 transition px-4 py-4 flex flex-col gap-2"
							on:click={() => openAgent(agent.id)}
						>
							<div class="flex items-center gap-2">
								<span class="text-2xl">{agent.emoji ?? '🤖'}</span>
								<div class="flex-1 min-w-0">
									<div class="text-base font-medium truncate">{agent.name}</div>
									<div class="text-xs text-gray-500 truncate">{agent.id}</div>
								</div>
								{#if agent.is_default}
									<span
										class="text-[10px] uppercase tracking-wide bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full px-2 py-0.5"
									>
										default
									</span>
								{/if}
							</div>
							{#if agent.model}
								<div class="text-xs text-gray-500 dark:text-gray-400 truncate">
									{agent.model}
								</div>
							{/if}
						</button>
					{/each}
				</div>
			{/if}
		</div>
	</div>
</div>
