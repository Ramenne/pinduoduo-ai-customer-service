package com.feifei.phonebridge;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.List;
import java.util.Set;

public final class BoundedXianyuActionsSelfTest {
    private static int checks;
    private static final String PACKAGE = "com.taobao.idlefish";

    public static void main(String[] args) throws Exception {
        nodeContracts();
        searches();
        imageContracts();
        tapContracts();
        System.out.println("BOUNDED_XIANYU_ACTIONS_SELF_TEST_PASS checks=" + checks);
    }

    private static void nodeContracts() {
        check(CloudTaskParametersContract.validate("action",Set.of("action","package","selector","value"),"set_node_text",PACKAGE,true,Set.of("kind","value"),"class","android.widget.EditText",null,"书签",null,null,null,null)==null);
        check(CloudTaskParametersContract.validate("action",Set.of("action","package","selector"),"tap_unique_node",PACKAGE,true,Set.of("kind","value"),"class","android.widget.EditText",null,null,null,null,null,null)!=null);
        check(node("tap_unique_node", null, Set.of("action", "package", "selector"), PACKAGE) == null);
        check(node("set_node_text", "a", Set.of("action", "package", "selector", "value"), PACKAGE) == null);
        check(node("set_node_text", "x".repeat(5000), Set.of("action", "package", "selector", "value"), PACKAGE) == null);
        check(node("set_node_text", "x".repeat(5001), Set.of("action", "package", "selector", "value"), PACKAGE).equals("invalid_node_text_value"));
        check(node("set_node_text", "", Set.of("action", "package", "selector", "value"), PACKAGE).equals("invalid_node_text_value"));
        check(node("set_node_text", 1, Set.of("action", "package", "selector", "value"), PACKAGE).equals("invalid_node_text_value"));
        check(node("set_node_text", " ", Set.of("action", "package", "selector", "value"), PACKAGE) == null);
        check(node("set_node_text", "a", Set.of("action", "package", "selector"), PACKAGE).equals("invalid_set_node_text_parameters"));
        check(node("click_unique_node", null, Set.of("action", "package", "selector"), PACKAGE) == null);
        check(node("click_unique_node", "a", Set.of("action", "package", "selector", "value"), PACKAGE).equals("invalid_click_unique_node_parameters"));
        check(node("click_unique_node", null, Set.of("action", "package", "selector"), "other").equals("cloud_node_package_not_allowed"));
        check(BoundedXianyuActions.validateSelector(true, Set.of("kind", "value"), "text", " ") != null);
        check(BoundedXianyuActions.validateSelector(true, Set.of("kind", "value", "extra"), "text", "title") != null);
        check(BoundedXianyuActions.validateSelector(true, Set.of("kind", "value"), " text ", "title") != null);
        check(BoundedXianyuActions.matches("text", "title", "", "title", "", false));
        check(!BoundedXianyuActions.matches("text", "title", "", " title ", "", false));
        check(!BoundedXianyuActions.matches("text", "A", "", "Ａ", "", false));
        check(!BoundedXianyuActions.matches("text", "title", "", "title more", "", false));
        check(!BoundedXianyuActions.matches("resource_id", "id", "id", "secret", "", true));
        check(BoundedXianyuActions.matches("description", "title", "", "different", "title", false));
        for (int bits = 0; bits < 16; bits++) {
            check(BoundedXianyuActions.maySet((bits & 1) != 0, (bits & 2) != 0,
                    (bits & 4) != 0, (bits & 8) != 0) == (bits == 7));
        }
        // Old overloads and legacy normalization remain unchanged.
        check(CloudTaskParametersContract.validate("action", Set.of("action", "package", "selector"),
                "click_node", PACKAGE, true, Set.of("kind", "value"), "text", "title") == null);
        check(XianyuNodeSelector.matches("text", "A", "", " Ａ ", "", false));
        check(CloudTaskParametersContract.validate("action", Set.of("action", "value"), "set_text", PACKAGE)
                .equals("unsupported_cloud_action"));
    }

    private static void tapContracts() {
        check(NodeTapPolicy.safe(true,true,false,91,1724,1189,1906,0,0,1280,2772));
        check(!NodeTapPolicy.safe(false,true,false,91,1724,1189,1906,0,0,1280,2772));
        check(!NodeTapPolicy.safe(true,false,false,91,1724,1189,1906,0,0,1280,2772));
        check(!NodeTapPolicy.safe(true,true,true,91,1724,1189,1906,0,0,1280,2772));
        check(!NodeTapPolicy.safe(true,true,false,-1,0,100,100,0,0,1280,2772));
        check(!NodeTapPolicy.safe(true,true,false,0,0,1280,2772,0,0,1280,2772));
        check(!NodeTapPolicy.safe(true,true,false,0,0,1281,100,0,0,1280,2772));
        check(!NodeTapPolicy.safe(true,true,false,1,1,1,100,0,0,1280,2772));
    }

    private static String node(String action, Object value, Set<String> fields, String pkg) {
        return CloudTaskParametersContract.validate("action", fields, action, pkg, true,
                Set.of("kind", "value"), "resource_id", "id", null, value, null, null, null, null);
    }

    private static void searches() {
        Node only = new Node(true);
        Node root = new Node(false, new Node(false, only));
        UniqueNodeSearch.Result<Node> result = search(root, 100, 10);
        check(result.matches == 1 && result.node == only && result.visited == 3 && !result.truncated);
        check(root.releases == 1 && root.children.get(0).releases == 1 && only.releases == 0);
        only.releases++;
        Node a = new Node(true), b = new Node(true);
        root = new Node(false, a, new Node(false, b));
        result = search(root, 100, 10);
        check(result.matches == 2 && result.node == null && !result.truncated && result.visited == 4);
        check(a.releases == 1 && b.releases == 1);
        root = new Node(false, new Node(true), new Node(false), new Node(false));
        result = search(root, 3, 10);
        check(result.truncated && result.node == null && root.releases == 1);
        check(root.children.get(0).releases == 1 && root.children.get(1).releases == 1);
        root = new Node(true, new Node(false));
        result = search(root, 100, 0);
        check(result.truncated && result.matches == 0 && result.node == null && root.releases == 1);
        root = new Node(false);
        result = search(root, 1, 10);
        check(!result.truncated && result.node == null && result.visited == 1);
        root = new Node(true);
        Thread.currentThread().interrupt();
        result = search(root, 100, 10);
        check(result.truncated && root.releases == 1);
        Thread.interrupted();
        root = new Node(true, new Node(false));
        try {
            UniqueNodeSearch.find(root, new UniqueNodeSearch.Access<Node>() {
                public boolean matches(Node n) { return n.match; }
                public int childCount(Node n) { throw new IllegalStateException("fixture failure"); }
                public Node child(Node n, int i) { return null; }
                public void release(Node n) { n.releases++; }
            }, 100, 10, () -> 0);
            throw new AssertionError("exception must propagate");
        } catch (IllegalStateException expected) {
            check(root.releases == 1);
        }
    }

    private static UniqueNodeSearch.Result<Node> search(Node root, int limit, long deadline) {
        return UniqueNodeSearch.find(root, new UniqueNodeSearch.Access<Node>() {
            public boolean matches(Node n) { return n.match; }
            public int childCount(Node n) { return n.children.size(); }
            public Node child(Node n, int index) { return n.children.get(index); }
            public void release(Node n) { n.releases++; }
        }, limit, deadline, () -> 0);
    }

    private static final class Node {
        final boolean match;
        final List<Node> children = new ArrayList<>();
        int releases;
        Node(boolean match, Node... children) { this.match = match; Collections.addAll(this.children, children); }
    }

    private static void imageContracts() throws Exception {
        // Real 1x1 PNG; byte validation does not claim Android bitmap/MediaStore acceptance.
        String encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB9sAAAAASUVORK5CYII=";
        byte[] bytes = Base64.getDecoder().decode(encoded);
        String sha = ImageImportContract.sha256(bytes);
        String filename = "oin-" + sha.substring(0, 16) + ".png";
        check(ImageImportContract.validate("image/png", filename, sha, encoded) == null);
        check(java.util.Arrays.equals(bytes, ImageImportContract.decode("image/png", filename, sha, encoded)));
        check(ImageImportContract.validate("image/png", filename, sha.toUpperCase(), encoded) == null);
        check(ImageImportContract.validate("image/gif", filename, sha, encoded).equals("invalid_image_mime_type"));
        check(ImageImportContract.validate("image/png", "../" + filename, sha, encoded).equals("invalid_image_filename"));
        check(ImageImportContract.validate("image/png", filename, "0", encoded).equals("invalid_image_sha256"));
        check(ImageImportContract.validate("image/png", filename, sha, "").equals("image_size_out_of_bounds"));
        check(ImageImportContract.validate("image/png", filename, sha, encoded + "\n").equals("invalid_image_base64"));
        check(ImageImportContract.validate("image/png", filename, sha, "a".repeat(ImageImportContract.MAX_BASE64_CHARS + 1))
                .equals("image_size_out_of_bounds"));
        check(ImageImportContract.validate("image/png", filename, sha, "----").equals("invalid_image_base64"));
        expectIo("image_sha256_mismatch", () -> ImageImportContract.decode("image/png", "oin-" + "0".repeat(16) + ".png", "0".repeat(64), encoded));
        expectIo("image_mime_mismatch", () -> ImageImportContract.decode("image/jpeg", "oin-" + sha.substring(0, 16) + ".jpg", sha, encoded));
        expectIo("invalid_image_base64", () -> ImageImportContract.decode("image/png", filename, sha, "AB=="));
        byte[] max = new byte[ImageImportContract.MAX_RAW_BYTES];
        check(ImageImportContract.readBounded(new ByteArrayInputStream(max)).length == max.length);
        expectIo("image_size_out_of_bounds", () -> ImageImportContract.readBounded(new ByteArrayInputStream(new byte[max.length + 1])));
        // An encoded input can fit the encoded cap but still decode to max+1.
        byte[] over = new byte[max.length + 1];
        String overSha = ImageImportContract.sha256(over);
        expectIo("image_size_out_of_bounds", () -> ImageImportContract.decode("image/png", "oin-" + overSha.substring(0, 16) + ".png", overSha, Base64.getEncoder().encodeToString(over)));
        expectIo("image_size_out_of_bounds", () -> ImageImportContract.readBounded(new ByteArrayInputStream(new byte[0])));
        expectIo("image_readback_unavailable", () -> ImageImportContract.readBounded(null));
        check(CloudTaskParametersContract.validate("action", Set.of("action", "package", "mime_type", "filename", "sha256", "data_base64"),
                "import_image", PACKAGE, false, Collections.emptySet(), null, null, null, null,
                "image/png", filename, sha, encoded) == null);
        check(CloudTaskParametersContract.validate("action", Set.of("action", "package", "mime_type", "filename", "sha256", "data_base64", "extra"),
                "import_image", PACKAGE, false, Collections.emptySet(), null, null, null, null,
                "image/png", filename, sha, encoded).equals("invalid_import_image_parameters"));
    }

    private interface IoAction { void run() throws IOException; }
    private static void expectIo(String message, IoAction action) throws IOException {
        try { action.run(); throw new AssertionError("expected=" + message); }
        catch (IOException actual) { check(message.equals(actual.getMessage())); }
    }
    private static void check(boolean condition) {
        checks++;
        if (!condition) throw new AssertionError("check=" + checks);
    }
}
