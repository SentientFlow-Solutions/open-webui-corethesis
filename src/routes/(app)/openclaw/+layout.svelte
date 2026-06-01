<script>
	import { onMount } from 'svelte';
	import { user } from '$lib/stores';

	let loaded = false;

	onMount(() => {
		loaded = true;
	});
</script>

{#if loaded && $user}
	<slot />
{/if}
