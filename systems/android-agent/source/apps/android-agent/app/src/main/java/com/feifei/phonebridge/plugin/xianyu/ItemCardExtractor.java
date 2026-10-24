package com.feifei.phonebridge.plugin.xianyu;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 首页推荐流商品卡提取器。
 *
 * 已验证范围仅为 xianyu_01/03/07 三份 HOME 真机样本。observe 的 BFS 输出没有
 * parent id，不能用“同 depth 向前扫描”跨卡猜测；本实现以宽 clickable
 * LinearLayout 在 BFS 节点序列中的相邻区间作为卡片边界，只在区间内提取字段。
 */
public final class ItemCardExtractor {

    private ItemCardExtractor() {
    }

    /** 兼容原入口；非 HOME 页面不执行商品规则。 */
    public static JSONArray extract(JSONObject observeJson) throws JSONException {
        if (XianYuPage.classify(observeJson) != XianYuPage.HOME) {
            return new JSONArray();
        }
        return extractNodes(UiNode.fromObserve(observeJson)).toJsonArray();
    }

    /** 生产规则的纯节点入口，供离线真机样本回放直接调用。 */
    static Extraction extractNodes(UiNode[] nodes) {
        List<ItemCard> cards = new ArrayList<>();
        Set<String> seenContainers = new HashSet<>();
        int incompleteCount = 0;
        if (nodes == null) {
            return new Extraction(cards, 0);
        }
        int minimumCardWidth = Math.max(1, findScreenWidth(nodes) / 3);

        for (int i = 0; i < nodes.length; i++) {
            UiNode anchor = nodes[i];
            if (!"¥".equals(anchor.text.trim())) {
                continue;
            }
            CardScope scope = findCardScope(nodes, i, minimumCardWidth);
            if (scope == null) {
                incompleteCount++;
                continue;
            }
            String containerKey = boundsKey(scope.container.bounds);
            if (!seenContainers.add(containerKey)) {
                continue;
            }

            String title = findTitle(nodes, i, scope);
            String price = findPrice(nodes, i, scope);
            String seller = findSeller(nodes, i, scope);
            if (title.isEmpty() || price.isEmpty() || seller.isEmpty()) {
                incompleteCount++;
                continue;
            }
            cards.add(new ItemCard(
                    anchor.index,
                    title,
                    price,
                    seller,
                    collectRawTexts(nodes, scope),
                    scope.container.bounds
            ));
        }
        return new Extraction(cards, incompleteCount);
    }

    private static int findScreenWidth(UiNode[] nodes) {
        int width = 0;
        for (UiNode node : nodes) {
            width = Math.max(width, node.bounds[2] - node.bounds[0]);
        }
        return width;
    }

    private static CardScope findCardScope(UiNode[] nodes, int anchorPosition, int minimumCardWidth) {
        int start = -1;
        UiNode container = null;
        for (int i = 0; i <= anchorPosition; i++) {
            if (isCardContainer(nodes[i], minimumCardWidth)) {
                start = i;
                container = nodes[i];
            }
        }
        if (start < 0) return null;
        int end = nodes.length;
        for (int i = start + 1; i < nodes.length; i++) {
            if (isCardContainer(nodes[i], minimumCardWidth)) {
                end = i;
                break;
            }
        }
        return anchorPosition < end ? new CardScope(container, start, end) : null;
    }

    private static boolean isCardContainer(UiNode node, int minimumCardWidth) {
        return node.clickable
                && node.className.endsWith("LinearLayout")
                && node.bounds[2] - node.bounds[0] >= minimumCardWidth;
    }

    private static String findTitle(UiNode[] nodes, int anchorPosition, CardScope scope) {
        String best = "";
        for (int i = scope.start + 1; i < anchorPosition; i++) {
            UiNode node = nodes[i];
            String text = cleanText(node.text);
            if (text.startsWith("pic")) text = text.substring(3).trim();
            if (!isTitleCandidate(text)) continue;
            if (text.length() > best.length()) best = text;
        }
        return best;
    }

    private static String findPrice(UiNode[] nodes, int anchorPosition, CardScope scope) {
        for (int i = anchorPosition + 1; i < scope.end; i++) {
            UiNode node = nodes[i];
            String text = cleanText(node.text);
            if ("¥".equals(text)) break;
            if (isNumber(text)) return text;
        }
        return "";
    }

    private static String findSeller(UiNode[] nodes, int anchorPosition, CardScope scope) {
        for (int i = anchorPosition + 1; i < scope.end; i++) {
            UiNode node = nodes[i];
            String text = cleanText(node.text);
            if (isSellerCandidate(text)) return text;
        }
        return "";
    }

    private static List<String> collectRawTexts(UiNode[] nodes, CardScope scope) {
        List<String> raw = new ArrayList<>();
        for (int i = scope.start + 1; i < scope.end; i++) {
            String text = cleanText(nodes[i].text);
            if (!text.isEmpty()) raw.add(text);
        }
        return raw;
    }

    private static boolean isTitleCandidate(String text) {
        return text.length() > 3
                && !isNumber(text)
                && !text.startsWith("¥")
                && !text.startsWith("市场价")
                && !text.contains("人想要")
                && !text.contains("信用")
                && !text.contains("|");
    }

    private static boolean isSellerCandidate(String text) {
        return !text.isEmpty()
                && !"pic".equals(text)
                && !isNumber(text)
                && !text.startsWith("¥")
                && !text.startsWith("市场价")
                && !text.contains("人想要")
                && !text.contains("信用")
                && !text.contains("|")
                && text.length() <= 40;
    }

    private static boolean isNumber(String text) {
        return text.matches("\\d+(\\.\\d+)?");
    }

    private static String cleanText(String text) {
        return text == null ? "" : text.trim();
    }

    private static String boundsKey(int[] bounds) {
        return bounds[0] + ":" + bounds[1] + ":" + bounds[2] + ":" + bounds[3];
    }

    private static final class CardScope {
        final UiNode container;
        final int start;
        final int end;

        CardScope(UiNode container, int start, int end) {
            this.container = container;
            this.start = start;
            this.end = end;
        }
    }

    public static JSONObject extractResult(JSONObject observeJson) throws JSONException {
        XianYuPage page = XianYuPage.classify(observeJson);
        Extraction extraction = page == XianYuPage.HOME
                ? extractNodes(UiNode.fromObserve(observeJson))
                : new Extraction(new ArrayList<>(), 0);
        JSONObject out = new JSONObject();
        out.put("success", true);
        out.put("page", page.name());
        out.put("rules_verified", page == XianYuPage.HOME);
        out.put("verification_scope", page == XianYuPage.HOME
                ? "offline_replay:xianyu_01,xianyu_03,xianyu_07"
                : "item_rule_disabled_outside_verified_home");
        out.put("live_endpoint_verified", false);
        out.put("card_count", extraction.cards.size());
        out.put("incomplete_card_count", extraction.incompleteCount);
        out.put("cards", extraction.toJsonArray());
        out.put("truncated", observeJson != null && observeJson.optBoolean("truncated", false));
        out.put("note", page == XianYuPage.HOME
                ? "sample replay verified; real /xianyu/items endpoint still requires device verification"
                : "no item extraction performed because current page is not verified HOME");
        return out;
    }

    static final class Extraction {
        final List<ItemCard> cards;
        final int incompleteCount;

        Extraction(List<ItemCard> cards, int incompleteCount) {
            this.cards = cards;
            this.incompleteCount = incompleteCount;
        }

        JSONArray toJsonArray() throws JSONException {
            JSONArray out = new JSONArray();
            for (ItemCard card : cards) out.put(card.toJson());
            return out;
        }
    }

    static final class ItemCard {
        final int anchorIndex;
        final String title;
        final String price;
        final String seller;
        final List<String> rawTexts;
        final int[] bounds;

        ItemCard(int anchorIndex, String title, String price, String seller,
                 List<String> rawTexts, int[] bounds) {
            this.anchorIndex = anchorIndex;
            this.title = title;
            this.price = price;
            this.seller = seller;
            this.rawTexts = rawTexts;
            this.bounds = bounds.clone();
        }

        JSONObject toJson() throws JSONException {
            JSONObject out = new JSONObject();
            out.put("index_anchor", anchorIndex);
            out.put("title", title);
            out.put("price", price);
            out.put("seller", seller);
            out.put("raw_texts", new JSONArray(rawTexts));
            out.put("bounds", new JSONArray(bounds));
            out.put("confidence", "sample_rule_complete");
            return out;
        }
    }
}
