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