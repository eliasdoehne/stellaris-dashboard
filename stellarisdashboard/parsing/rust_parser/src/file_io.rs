use std::io::Read;

use zip::ZipArchive;

#[derive(Debug)]
pub struct SaveFile {
    pub filename: String,
    pub game_id: String,
    pub meta: String,
    pub gamestate: String,
}


pub fn load_save_content<'a>(filename: &str) -> Result<SaveFile, &str> {
    let save_path = std::path::Path::new(filename);

    let err_msg = "Could not determine game ID";
    let game_id = match save_path.parent() {
        Some(p) => match p.file_name() {
            Some(p) => match p.to_str() {
                Some(s) => s.to_string(),
                _ => return Err(err_msg)
            },
            _ => return Err(err_msg)
        },
        None => return Err(err_msg),
    };

    let zipfile = match std::fs::File::open(save_path) {
        Ok(zf) => zf,
        Err(_) => return Err("Failed to open file"),
    };

    let mut archive = match ZipArchive::new(zipfile) {
        Ok(a) => a,
        Err(_) => return Err("Failed to read zip archive"),
    };

    let meta = match read_file_from_archive(&mut archive, "meta") {
        Ok(content) => content,
        Err(e) => return Err(e)
    };
    let gamestate = match read_file_from_archive(&mut archive, "gamestate") {
        Ok(content) => content,
        Err(e) => return Err(e)
    };

    Ok(SaveFile { filename: String::from(filename), game_id, meta, gamestate })
}


pub fn read_file_from_archive(archive: &mut ZipArchive<std::fs::File>, fname: &str) -> Result<String, &'static str> {
    let mut file_in_zip = match archive.by_name(fname) {
        Ok(file) => file,
        Err(_) => {
            return Err("Could not locate file in zip archive");
        }
    };
    let mut content = String::new();
    file_in_zip.read_to_string(&mut content).expect(
        "Failed to read file contents from zip archive"
    );
    Ok(content)
}


/// Read one member of the save's zip archive into a pre-sized buffer.
///
/// `fast`: read the whole archive into memory and inflate the raw deflate stream with libdeflate
/// (verifying the CRC); otherwise (or for members that are not deflated) use the zip crate's
/// streaming reader.
pub fn read_member_bytes(filename: &str, member: &str, fast: bool) -> Result<Vec<u8>, String> {
    let archive_bytes = std::fs::read(filename).map_err(|e| format!("Failed to open file: {e}"))?;
    let mut archive = ZipArchive::new(std::io::Cursor::new(archive_bytes.as_slice()))
        .map_err(|e| format!("Failed to read zip archive: {e}"))?;
    let (start, csize, size, crc, deflated) = {
        let file = archive
            .by_name(member)
            .map_err(|_| format!("Could not locate {member} in zip archive"))?;
        (
            file.data_start(),
            file.compressed_size() as usize,
            file.size() as usize,
            file.crc32(),
            file.compression() == zip::CompressionMethod::Deflated,
        )
    };
    if fast && deflated {
        if let Some(start) = start {
            let start = start as usize;
            if start + csize <= archive_bytes.len() {
                let mut out = vec![0u8; size];
                let mut d = libdeflater::Decompressor::new();
                let n = d
                    .deflate_decompress(&archive_bytes[start..start + csize], &mut out)
                    .map_err(|e| format!("Failed to inflate {member}: {e}"))?;
                if n != size {
                    return Err(format!("Unexpected size of {member}"));
                }
                if crc32fast::hash(&out) != crc {
                    return Err(format!("CRC mismatch in {member}"));
                }
                return Ok(out);
            }
        }
    }
    let mut file = archive
        .by_name(member)
        .map_err(|_| format!("Could not locate {member} in zip archive"))?;
    let mut out = Vec::with_capacity(size + 1);
    file.read_to_end(&mut out).map_err(|e| format!("Failed to read {member}: {e}"))?;
    Ok(out)
}