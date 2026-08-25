package io.github.kindlekidsphotoexport;

import android.app.Activity;
import android.content.ClipData;
import android.content.ComponentName;
import android.content.ContentUris;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.StatFs;
import android.provider.MediaStore;
import android.util.Log;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.InputStream;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Copies protected Amazon Kids camera MediaStore items to a temporary public
 * Download directory for an ADB pull, or exposes their URIs to the built-in
 * Fire Email composer for the original local SMTP capture workflow.
 */
public final class ExportActivity extends Activity {
    private static final String TAG = "KidsPhotoExport";
    private static final String KIDS_ROOT =
            "/data/securedStorageLocation/com.android.camera2/FreeTime/";
    private static final String EXPORT_DIR = "Kindle-Kids-Photo-Export";
    private TextView statusView;

    private static final class MediaRef {
        final long id;
        final int mediaType;
        final String name;
        final long dateAdded;
        final long size;

        MediaRef(long id, int mediaType, String name, long dateAdded, long size) {
            this.id = id;
            this.mediaType = mediaType;
            this.name = name;
            this.dateAdded = dateAdded;
            this.size = size;
        }
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        statusView = new TextView(this);
        statusView.setPadding(32, 32, 32, 32);
        setContentView(statusView);
        final Intent intent = getIntent();
        new Thread(new Runnable() {
            @Override
            public void run() {
                if (intent.getBooleanExtra("list_profiles", false)) {
                    listProfiles();
                } else if (intent.getBooleanExtra("copy_to_download", false)) {
                    copyMediaToDownload(intent);
                } else if (intent.getBooleanExtra("forward_to_email", false)) {
                    forwardMediaToEmail(intent);
                } else {
                    showStatus("Launch this helper from the supplied Mac scripts.\n"
                            + "No photos are changed or deleted.");
                }
            }
        }, "kindle-kids-photo-export").start();
    }

    private void copyMediaToDownload(Intent intent) {
        String profile = intent.getStringExtra("profile_name");
        String outputName = intent.getStringExtra("output_dir");
        if (profile == null || profile.length() == 0
                || profile.contains("/") || profile.contains("..")) {
            showStatus("A valid profile_name is required.");
            Log.e(TAG, "Missing or invalid profile_name");
            return;
        }
        if (outputName == null || outputName.length() == 0) {
            outputName = EXPORT_DIR;
        }
        if (outputName.contains("/") || outputName.contains("..")) {
            showStatus("A valid output_dir is required.");
            Log.e(TAG, "Missing or invalid output_dir");
            return;
        }

        List<MediaRef> refs = new ArrayList<>();
        String selection = MediaStore.MediaColumns.DATA + " LIKE ? AND ("
                + MediaStore.Files.FileColumns.MEDIA_TYPE + "=? OR "
                + MediaStore.Files.FileColumns.MEDIA_TYPE + "=?)";
        String[] args = {
                KIDS_ROOT + profile + "/%",
                Integer.toString(MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE),
                Integer.toString(MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO)
        };
        try (Cursor cursor = queryKidsMedia(selection, args)) {
            if (cursor == null) {
                throw new IllegalStateException("MediaStore returned no cursor");
            }
            int idColumn = cursor.getColumnIndexOrThrow(MediaStore.Files.FileColumns._ID);
            int typeColumn = cursor.getColumnIndexOrThrow(
                    MediaStore.Files.FileColumns.MEDIA_TYPE);
            int nameColumn = cursor.getColumnIndexOrThrow(
                    MediaStore.MediaColumns.DISPLAY_NAME);
            int dateColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED);
            int sizeColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE);
            while (cursor.moveToNext()) {
                refs.add(new MediaRef(cursor.getLong(idColumn), cursor.getInt(typeColumn),
                        cursor.getString(nameColumn), cursor.getLong(dateColumn),
                        cursor.getLong(sizeColumn)));
            }
        } catch (Exception error) {
            Log.e(TAG, "Unable to query direct-copy batch", error);
            showStatus("Unable to query media:\n" + error.getMessage());
            return;
        }

        Collections.sort(refs, new Comparator<MediaRef>() {
            @Override
            public int compare(MediaRef left, MediaRef right) {
                if (left.dateAdded != right.dateAdded) {
                    return left.dateAdded > right.dateAdded ? -1 : 1;
                }
                return left.id == right.id ? 0 : (left.id > right.id ? -1 : 1);
            }
        });

        File root = new File(Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS), outputName);
        root.mkdirs();
        long declaredBytes = 0;
        for (MediaRef ref : refs) {
            declaredBytes += Math.max(0, ref.size);
        }
        long availableBytes = new StatFs(root.getAbsolutePath()).getAvailableBytes();
        if (declaredBytes > availableBytes) {
            Log.e(TAG, "COPY_ABORT reason=NO_SPACE expected_bytes=" + declaredBytes
                    + " available_bytes=" + availableBytes + " output="
                    + root.getAbsolutePath());
            showStatus("Not enough free tablet storage for direct staging\nRequired: "
                    + declaredBytes + " bytes\nAvailable: " + availableBytes + " bytes");
            return;
        }
        int copied = 0;
        int failed = 0;
        long totalBytes = 0;
        for (int index = 0; index < refs.size(); index++) {
            MediaRef ref = refs.get(index);
            Uri collection = ref.mediaType == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO
                    ? MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                    : MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
            Uri uri = ContentUris.withAppendedId(collection, ref.id);
            String filename = ref.name == null || ref.name.length() == 0
                    ? Long.toString(ref.id) : new File(ref.name).getName();
            File target = new File(root, filename);
            if (target.exists()) {
                target = new File(root, ref.id + "__" + filename);
            }
            long bytes = 0;
            try (InputStream input = getContentResolver().openInputStream(uri);
                 FileOutputStream output = new FileOutputStream(target)) {
                if (input == null) {
                    throw new IllegalStateException("MediaStore returned no stream");
                }
                byte[] buffer = new byte[1024 * 1024];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                    bytes += read;
                }
                if (ref.dateAdded > 0) {
                    target.setLastModified(ref.dateAdded * 1000L);
                }
                copied++;
                totalBytes += bytes;
                Log.i(TAG, "COPY_OK index=" + index + " id=" + ref.id
                        + " bytes=" + bytes + " name=" + filename);
            } catch (Exception error) {
                failed++;
                target.delete();
                Log.e(TAG, "COPY_FAIL index=" + index + " id=" + ref.id
                        + " name=" + filename, error);
            }
        }
        Log.i(TAG, "COPY_COMPLETE profile=" + profile + " expected=" + refs.size()
                + " copied=" + copied + " failed=" + failed + " bytes=" + totalBytes
                + " output=" + root.getAbsolutePath());
        showStatus("Direct USB staging complete\nProfile: " + profile
                + "\nCopied: " + copied + " / " + refs.size()
                + "\nFailed: " + failed + "\nBytes: " + totalBytes);
    }

    private Cursor queryKidsMedia(String selection, String[] selectionArgs) {
        Uri files = MediaStore.Files.getContentUri("external");
        String[] projection = {
                MediaStore.Files.FileColumns._ID,
                MediaStore.Files.FileColumns.MEDIA_TYPE,
                MediaStore.MediaColumns.DATA,
                MediaStore.MediaColumns.DISPLAY_NAME,
                MediaStore.MediaColumns.DATE_ADDED,
                MediaStore.MediaColumns.SIZE
        };
        return getContentResolver().query(files, projection, selection,
                selectionArgs, null);
    }

    private void listProfiles() {
        Map<String, Integer> counts = new LinkedHashMap<>();
        String selection = MediaStore.MediaColumns.DATA + " LIKE ? AND ("
                + MediaStore.Files.FileColumns.MEDIA_TYPE + "=? OR "
                + MediaStore.Files.FileColumns.MEDIA_TYPE + "=?)";
        String[] args = {
                KIDS_ROOT + "%",
                Integer.toString(MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE),
                Integer.toString(MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO)
        };
        try (Cursor cursor = queryKidsMedia(selection, args)) {
            if (cursor == null) {
                throw new IllegalStateException("MediaStore returned no cursor");
            }
            int dataColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATA);
            while (cursor.moveToNext()) {
                String path = cursor.getString(dataColumn);
                if (path == null || !path.startsWith(KIDS_ROOT)) {
                    continue;
                }
                String remainder = path.substring(KIDS_ROOT.length());
                int slash = remainder.indexOf('/');
                if (slash <= 0) {
                    continue;
                }
                String profile = remainder.substring(0, slash);
                Integer oldCount = counts.get(profile);
                counts.put(profile, oldCount == null ? 1 : oldCount + 1);
            }
            if (counts.isEmpty()) {
                Log.i(TAG, "PROFILE_NONE");
                showStatus("No Amazon Kids camera profiles were found.");
                return;
            }
            StringBuilder display = new StringBuilder("Amazon Kids camera profiles:\n");
            for (Map.Entry<String, Integer> entry : counts.entrySet()) {
                Log.i(TAG, "PROFILE_FOUND name=" + entry.getKey()
                        + " total=" + entry.getValue());
                display.append(entry.getKey()).append(" — ")
                        .append(entry.getValue()).append(" items\n");
            }
            showStatus(display.toString());
        } catch (Exception error) {
            Log.e(TAG, "Unable to list Kids profiles", error);
            showStatus("Unable to list Kids profiles:\n" + error.getMessage());
        }
    }

    private void forwardMediaToEmail(Intent intent) {
        String profile = intent.getStringExtra("profile_name");
        if (profile == null || profile.length() == 0
                || profile.contains("/") || profile.contains("..")) {
            showStatus("A valid profile_name is required. Run the profile discovery step.");
            Log.e(TAG, "Missing or invalid profile_name");
            return;
        }
        int offset = Math.max(0, intent.getIntExtra("offset", 0));
        int requestedLimit = Math.max(1, intent.getIntExtra("limit", 1));
        int limit = Math.min(299, requestedLimit);
        List<MediaRef> refs = new ArrayList<>();
        ArrayList<Uri> uris = new ArrayList<>();
        List<String> names = new ArrayList<>();
        List<Integer> mediaTypes = new ArrayList<>();

        String selection = MediaStore.MediaColumns.DATA + " LIKE ? AND ("
                + MediaStore.Files.FileColumns.MEDIA_TYPE + "=? OR "
                + MediaStore.Files.FileColumns.MEDIA_TYPE + "=?)";
        String[] args = {
                KIDS_ROOT + profile + "/%",
                Integer.toString(MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE),
                Integer.toString(MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO)
        };
        try (Cursor cursor = queryKidsMedia(selection, args)) {
            if (cursor == null) {
                throw new IllegalStateException("MediaStore returned no cursor");
            }
            int idColumn = cursor.getColumnIndexOrThrow(MediaStore.Files.FileColumns._ID);
            int typeColumn = cursor.getColumnIndexOrThrow(
                    MediaStore.Files.FileColumns.MEDIA_TYPE);
            int nameColumn = cursor.getColumnIndexOrThrow(
                    MediaStore.MediaColumns.DISPLAY_NAME);
            int dateColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED);
            int sizeColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE);
            while (cursor.moveToNext()) {
                refs.add(new MediaRef(
                        cursor.getLong(idColumn),
                        cursor.getInt(typeColumn),
                        cursor.getString(nameColumn),
                        cursor.getLong(dateColumn),
                        cursor.getLong(sizeColumn)));
            }
        } catch (Exception error) {
            Log.e(TAG, "Unable to prepare Email forwarding batch", error);
            showStatus("Unable to prepare Email batch:\n" + error.getMessage());
            return;
        }

        Collections.sort(refs, new Comparator<MediaRef>() {
            @Override
            public int compare(MediaRef left, MediaRef right) {
                if (left.dateAdded != right.dateAdded) {
                    return left.dateAdded > right.dateAdded ? -1 : 1;
                }
                if (left.mediaType != right.mediaType) {
                    if (left.mediaType == MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE) {
                        return -1;
                    }
                    if (right.mediaType == MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE) {
                        return 1;
                    }
                }
                return left.id == right.id ? 0 : (left.id > right.id ? -1 : 1);
            }
        });

        int imageCount = 0;
        int videoCount = 0;
        int end = Math.min(refs.size(), offset + limit);
        for (int index = Math.min(offset, refs.size()); index < end; index++) {
            MediaRef ref = refs.get(index);
            Uri collection;
            if (ref.mediaType == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO) {
                collection = MediaStore.Video.Media.EXTERNAL_CONTENT_URI;
                videoCount++;
            } else {
                collection = MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
                imageCount++;
            }
            uris.add(ContentUris.withAppendedId(collection, ref.id));
            names.add(ref.name == null ? Long.toString(ref.id) : ref.name);
            mediaTypes.add(ref.mediaType);
        }

        int count = uris.size();
        Log.i(TAG, "FORWARD_START profile=" + profile + " offset=" + offset
                + " count=" + count + " requested=" + requestedLimit
                + " limit=" + limit + " total=" + refs.size()
                + " images=" + imageCount + " videos=" + videoCount);
        if (count == 0) {
            showStatus("No camera items at offset " + offset + ".");
            return;
        }

        writeBatchManifest(offset, uris, names, mediaTypes);
        String mime = imageCount > 0 && videoCount == 0
                ? "image/*" : (videoCount > 0 && imageCount == 0 ? "video/*" : "*/*");
        Intent email = new Intent(Intent.ACTION_SEND_MULTIPLE);
        email.setComponent(new ComponentName(
                "com.android.email", "com.android.email.activity.MessageCompose"));
        email.setType(mime);
        email.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        email.putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris);
        email.putExtra(Intent.EXTRA_EMAIL, new String[]{"capture@local.invalid"});
        email.putExtra(Intent.EXTRA_SUBJECT,
                "Kindle Kids photo batch offset=" + offset + " count=" + count);
        ClipData clip = ClipData.newRawUri("Kindle Kids camera batch", uris.get(0));
        for (int index = 1; index < uris.size(); index++) {
            clip.addItem(new ClipData.Item(uris.get(index)));
        }
        email.setClipData(clip);
        for (Uri uri : uris) {
            grantUriPermission("com.android.email", uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION);
        }
        showStatus("Opening Email with " + count + " original item(s)\n"
                + "Offset: " + offset + "\nFirst: " + names.get(0)
                + "\nLast: " + names.get(names.size() - 1));
        try {
            startActivity(email);
        } catch (Exception error) {
            Log.e(TAG, "Unable to open Email composer", error);
            showStatus("Email composer failed:\n" + error.getMessage());
        }
    }

    private void writeBatchManifest(int offset, List<Uri> uris,
                                    List<String> names, List<Integer> mediaTypes) {
        File root = new File(Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS), EXPORT_DIR);
        root.mkdirs();
        File output = new File(root,
                "forward-batch-" + offset + "-" + uris.size() + ".tsv");
        try (PrintWriter writer = new PrintWriter(new FileWriter(output, false))) {
            writer.println("batch_position\tabsolute_index\tmedia_type\tfilename\turi");
            for (int index = 0; index < uris.size(); index++) {
                String type = mediaTypes.get(index)
                        == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO
                        ? "video" : "image";
                writer.println((index + 1) + "\t" + (offset + index) + "\t"
                        + type + "\t" + safe(names.get(index)) + "\t"
                        + safe(uris.get(index).toString()));
            }
            Log.i(TAG, "FORWARD_MANIFEST path=" + output.getAbsolutePath()
                    + " count=" + uris.size());
        } catch (Exception error) {
            Log.e(TAG, "Unable to write forwarding batch manifest", error);
        }
    }

    private static String safe(String value) {
        return value == null ? "" : value.replace('\t', ' ').replace('\n', ' ')
                .replace('\r', ' ');
    }

    private void showStatus(final String text) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                statusView.setText(text);
            }
        });
    }
}
