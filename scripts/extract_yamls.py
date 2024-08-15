import os
import zipfile
import argparse


def extract_datasets_from_zip(zip_folder, output_folder, yaml_folder=None):
    # Создаем выходную директорию, если её не существует
    os.makedirs(output_folder, exist_ok=True)

    # Проходимся по всем zip-файлам в директории
    for root, dirs, files in os.walk(zip_folder):
        for file in files:
            if file.endswith('.zip'):
                zip_path = os.path.join(root, file)

                with zipfile.ZipFile(zip_path) as zip_ref:
                    # Получаем список файлов внутри zip-архива
                    for zip_info in zip_ref.infolist():
                        if "metadata.yaml" in zip_info.filename:
                            continue

                        if yaml_folder:
                            if yaml_folder in zip_info.filename:
                                # Извлекаем относительный путь, начиная с datasets_folder
                                start_idx = zip_info.filename.index(yaml_folder)
                                relative_path = zip_info.filename[start_idx:]
                            else:
                                continue
                        else:
                            # Если datasets_folder не указан, используем весь путь
                            start_idx = zip_info.filename.index("/") + 1
                            relative_path = zip_info.filename[start_idx:]

                        filename = os.path.basename(relative_path)
                        dirname = os.path.dirname(relative_path)
                        # Полный путь к файлу на выходе
                        extract_path = os.path.join(output_folder, relative_path)

                        # Создаем нужные директории на выходе
                        os.makedirs(os.path.dirname(extract_path), exist_ok=True)

                        # Извлекаем файл в указанное место
                        with open(extract_path, 'wb') as out_file:
                            out_file.write(zip_ref.read(zip_info.filename))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract datasets from zip files.")
    parser.add_argument('zip_folder', help='Path to the folder containing zip files.')
    parser.add_argument('output_folder', help='Path to the output folder where files will be extracted.')
    parser.add_argument('--yaml_folder', help='Optional: Name of the datasets folder inside the zip files.', default=None)

    args = parser.parse_args()

    extract_datasets_from_zip(args.zip_folder, args.output_folder, args.yaml_folder)
