import { WEBUI_API_BASE_URL } from '$lib/constants';

export type LangfuseConfig = { enabled: boolean };

export type UsageTotals = {
	tokens: number;
	cost: number;
	requests: number;
	avgLatency: number;
};

export type ModelUsage = { model: string; tokens: number; cost: number; requests: number };
export type DailyUsage = { date: string; tokens: number; cost: number; requests: number };

export type UsageOverview = {
	enabled: boolean;
	totals: UsageTotals;
	byModel: ModelUsage[];
	daily: DailyUsage[];
};

export type ChatUsage = { enabled: boolean; turns: number; cost: number; avgLatency: number };

const handle = async (res: Response) => {
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			detail = body?.detail ?? detail;
		} catch {
			/* ignore */
		}
		throw new Error(detail || `Request failed: ${res.status}`);
	}
	return res.json();
};

export const getLangfuseConfig = async (token: string): Promise<LangfuseConfig> => {
	return fetch(`${WEBUI_API_BASE_URL}/langfuse/config`, {
		headers: { authorization: `Bearer ${token}` }
	}).then(handle);
};

export const getUsageOverview = async (token: string, days = 30): Promise<UsageOverview> => {
	return fetch(`${WEBUI_API_BASE_URL}/langfuse/usage/overview?days=${days}`, {
		headers: { authorization: `Bearer ${token}` }
	}).then(handle);
};

export const getChatUsage = async (token: string, chatId: string): Promise<ChatUsage> => {
	return fetch(`${WEBUI_API_BASE_URL}/langfuse/usage/chat/${encodeURIComponent(chatId)}`, {
		headers: { authorization: `Bearer ${token}` }
	}).then(handle);
};
