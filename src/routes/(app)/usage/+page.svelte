<script lang="ts">
	import { mobile, showSidebar } from '$lib/stores';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import Dashboard from '$lib/components/usage/Dashboard.svelte';
</script>

<svelte:head>
	<title>Usage</title>
</svelte:head>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	{#if $mobile}
		<nav class="px-2 pt-1.5 backdrop-blur-xl w-full drag-region">
			<div class="flex items-center">
				<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center">
					<button
						class="cursor-pointer p-1.5 flex rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={() => showSidebar.set(!$showSidebar)}
						aria-label="Toggle sidebar"
					>
						<div class="self-center">
							<Sidebar className="size-5" />
						</div>
					</button>
				</div>
			</div>
		</nav>
	{/if}

	<div class="flex-1 min-h-0 overflow-hidden">
		<Dashboard />
	</div>
</div>
